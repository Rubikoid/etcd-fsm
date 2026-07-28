import inspect
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel

from ._typing import pydantic_generic_argument
from .base import BaseState, Command, validate_fsm_key
from .errors import SchemaDefinitionError

type StateModel = type[BaseState[Any]]
type CommandModel = type[Command[Any]]


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    model: CommandModel
    discriminator: Enum
    result_type: Any


class FSMSchema[S: Enum]:
    def __init__(
        self,
        *,
        name: str,
        namespace: str,
        state_enum: type[S],
        states: Iterable[StateModel],
        command_enum: type[Enum] | None = None,
        commands: Iterable[CommandModel] = (),
    ) -> None:
        self.name = _validate_name(name)
        self.namespace = _validate_namespace(namespace)
        self.state_enum = state_enum
        self.command_enum = command_enum

        _reject_enum_aliases(state_enum, "state")
        if command_enum is not None:
            _reject_enum_aliases(command_enum, "command")

        state_mapping = self._compile_states(tuple(states))
        command_mapping = self._compile_commands(tuple(commands))
        self._states = MappingProxyType(state_mapping)
        self._commands = MappingProxyType(command_mapping)
        self._state_models = frozenset(state_mapping.values())
        self._command_models = frozenset(
            definition.model for definition in command_mapping.values()
        )

    @property
    def states(self) -> Mapping[S, StateModel]:
        return self._states

    @property
    def commands(self) -> Mapping[Enum, CommandDefinition]:
        return self._commands

    def state_model(self, discriminator: S) -> StateModel:
        try:
            return self._states[discriminator]
        except KeyError as error:
            raise SchemaDefinitionError(
                f"{discriminator!r} is not registered in schema {self.name!r}"
            ) from error

    def command_definition(self, model: CommandModel) -> CommandDefinition:
        for definition in self._commands.values():
            if definition.model is model:
                return definition
        raise SchemaDefinitionError(
            f"command model {model.__qualname__} is not registered in schema {self.name!r}"
        )

    def contains_state_model(self, model: object) -> bool:
        return any(model is registered for registered in self._state_models)

    def contains_command_model(self, model: object) -> bool:
        return any(model is registered for registered in self._command_models)

    def validate_state_value(self, value: BaseState[Any]) -> BaseState[Any]:
        if type(value.state) is not self.state_enum:
            raise SchemaDefinitionError(
                f"{type(value).__qualname__} uses a discriminator from another enum"
            )
        model = self._states.get(cast(S, value.state))
        if model is None or type(value) is not model:
            raise SchemaDefinitionError(
                f"{type(value).__qualname__} does not match a state registered in "
                f"schema {self.name!r}"
            )
        validated = model.model_validate(value.model_dump(mode="python"))
        validate_fsm_key(validated.fsm_key())
        return validated

    def validate_command_value(self, value: Command[Any]) -> Command[Any]:
        field = type(value).model_fields.get("command")
        if field is None:
            raise SchemaDefinitionError(
                f"command model {type(value).__qualname__} has no 'command' field"
            )
        discriminator = vars(value)["command"]
        if self.command_enum is None or type(discriminator) is not self.command_enum:
            raise SchemaDefinitionError(
                f"{type(value).__qualname__} uses a command discriminator from another enum"
            )
        definition = self._commands.get(discriminator)
        if definition is None or type(value) is not definition.model:
            raise SchemaDefinitionError(
                f"{type(value).__qualname__} does not match a command registered in "
                f"schema {self.name!r}"
            )
        validated = definition.model.model_validate(value.model_dump(mode="python"))
        validate_fsm_key(validated.fsm_key())
        return validated

    def _compile_states(self, models: tuple[StateModel, ...]) -> dict[S, StateModel]:
        mapping: dict[S, StateModel] = {}
        seen_models: set[StateModel] = set()

        for model in models:
            if model in seen_models:
                raise SchemaDefinitionError(
                    f"state model {model.__qualname__} is registered more than once"
                )
            seen_models.add(model)

            if not isinstance(model, type) or not issubclass(model, BaseState):
                raise SchemaDefinitionError(f"{model!r} is not a BaseState model")
            if inspect.isabstract(model):
                raise SchemaDefinitionError(f"state model {model.__qualname__} is abstract")
            _validate_model_contract(model, "state")

            discriminator = _literal_discriminator(
                model=model,
                field_name="state",
                enum_type=self.state_enum,
            )
            typed_discriminator = cast(S, discriminator)
            if typed_discriminator in mapping:
                other = mapping[typed_discriminator]
                raise SchemaDefinitionError(
                    f"state discriminator {discriminator!r} is declared by both "
                    f"{other.__qualname__} and {model.__qualname__}"
                )
            mapping[typed_discriminator] = model

        expected = set(self.state_enum)
        missing = expected.difference(mapping)
        if missing:
            formatted = ", ".join(member.name for member in sorted(missing, key=lambda x: x.name))
            raise SchemaDefinitionError(
                f"schema {self.name!r} is missing State Models for: {formatted}"
            )
        if not mapping:
            raise SchemaDefinitionError(
                f"schema {self.name!r} must register at least one State Model"
            )

        return mapping

    def _compile_commands(
        self,
        models: tuple[CommandModel, ...],
    ) -> dict[Enum, CommandDefinition]:
        if self.command_enum is None:
            if models:
                raise SchemaDefinitionError(
                    "command_enum is required when Command Models are registered"
                )
            return {}

        mapping: dict[Enum, CommandDefinition] = {}
        seen_models: set[CommandModel] = set()

        for model in models:
            if model in seen_models:
                raise SchemaDefinitionError(
                    f"command model {model.__qualname__} is registered more than once"
                )
            seen_models.add(model)

            if not isinstance(model, type) or not issubclass(model, Command):
                raise SchemaDefinitionError(f"{model!r} is not a Command model")
            if inspect.isabstract(model):
                raise SchemaDefinitionError(f"command model {model.__qualname__} is abstract")
            _validate_model_contract(model, "command")

            discriminator = _literal_discriminator(
                model=model,
                field_name="command",
                enum_type=self.command_enum,
            )
            try:
                result_type = pydantic_generic_argument(model, Command)
            except (LookupError, TypeError) as error:
                raise SchemaDefinitionError(
                    f"command model {model.__qualname__} must specialize Command[ResultT]"
                ) from error

            if result_type is Any:
                raise SchemaDefinitionError(
                    f"command model {model.__qualname__} has an unresolved result type"
                )
            if discriminator in mapping:
                other = mapping[discriminator].model
                raise SchemaDefinitionError(
                    f"command discriminator {discriminator!r} is declared by both "
                    f"{other.__qualname__} and {model.__qualname__}"
                )
            mapping[discriminator] = CommandDefinition(
                model=model,
                discriminator=discriminator,
                result_type=result_type,
            )

        expected = set(self.command_enum)
        missing = expected.difference(mapping)
        if missing:
            formatted = ", ".join(member.name for member in sorted(missing, key=lambda x: x.name))
            raise SchemaDefinitionError(
                f"schema {self.name!r} is missing Command Models for: {formatted}"
            )

        return mapping


def _literal_discriminator(
    *,
    model: type[BaseModel],
    field_name: str,
    enum_type: type[Enum],
) -> Enum:
    local_annotations = model.__dict__.get("__annotations__", {})
    if field_name not in local_annotations:
        raise SchemaDefinitionError(
            f"{model.__qualname__} must declare {field_name!r} locally as one Literal"
        )

    try:
        annotation = get_type_hints(model, include_extras=True)[field_name]
    except (NameError, TypeError) as error:
        raise SchemaDefinitionError(
            f"cannot resolve {model.__qualname__}.{field_name}: {error}"
        ) from error

    if get_origin(annotation) is not Literal:
        raise SchemaDefinitionError(
            f"{model.__qualname__}.{field_name} must be annotated as Literal[member]"
        )
    arguments = get_args(annotation)
    if len(arguments) != 1:
        raise SchemaDefinitionError(
            f"{model.__qualname__}.{field_name} must contain exactly one Literal member"
        )

    discriminator = arguments[0]
    if type(discriminator) is not enum_type:
        raise SchemaDefinitionError(
            f"{model.__qualname__}.{field_name} uses {type(discriminator).__qualname__}, "
            f"expected {enum_type.__qualname__}"
        )

    field = model.model_fields.get(field_name)
    if field is None or field.is_required():
        raise SchemaDefinitionError(
            f"{model.__qualname__}.{field_name} must default to {discriminator!r}"
        )
    if field.default is not discriminator:
        raise SchemaDefinitionError(
            f"{model.__qualname__}.{field_name} default must be the Literal member "
            f"{discriminator!r}"
        )

    return discriminator


def _reject_enum_aliases(enum_type: type[Enum], role: str) -> None:
    if not isinstance(enum_type, type) or not issubclass(enum_type, Enum):
        raise SchemaDefinitionError(f"{role}_enum must be an Enum type")
    if len(enum_type.__members__) != len(list(enum_type)):
        raise SchemaDefinitionError(
            f"{role} enum {enum_type.__qualname__} must not contain aliases"
        )


def _validate_model_contract(model: StateModel | CommandModel, role: str) -> None:
    if model.model_config.get("frozen") is not True:
        raise SchemaDefinitionError(f"{role} model {model.__qualname__} must be frozen")

    method = model.fsm_key
    if inspect.iscoroutinefunction(method):
        raise SchemaDefinitionError(
            f"{role} model {model.__qualname__}.fsm_key must be synchronous"
        )

    signature = inspect.signature(method)
    if len(signature.parameters) != 1:
        raise SchemaDefinitionError(
            f"{role} model {model.__qualname__}.fsm_key must accept only self"
        )
    try:
        return_type = get_type_hints(method).get("return")
    except (NameError, TypeError) as error:
        raise SchemaDefinitionError(
            f"cannot resolve {role} model {model.__qualname__}.fsm_key: {error}"
        ) from error
    if return_type is not str:
        raise SchemaDefinitionError(f"{role} model {model.__qualname__}.fsm_key must return str")


def _validate_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise SchemaDefinitionError("schema name must be a non-empty string")
    if "/" in name:
        raise SchemaDefinitionError("schema name must not contain '/'")
    return name


def _validate_namespace(namespace: str) -> str:
    if not isinstance(namespace, str) or not namespace.startswith("/"):
        raise SchemaDefinitionError("schema namespace must be an absolute etcd path")
    if namespace == "/" or namespace.endswith("/"):
        raise SchemaDefinitionError("schema namespace must not be root or end with '/'")
    if "\x00" in namespace:
        raise SchemaDefinitionError("schema namespace must not contain NUL")

    segments = namespace[1:].split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise SchemaDefinitionError(f"schema namespace must be normalized: {namespace!r}")
    return namespace
