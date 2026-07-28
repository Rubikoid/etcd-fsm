import inspect
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import (
    Any,
    Literal,
    TypeVar,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from pydantic import BaseModel

from ._typing import (
    flatten_union,
    pydantic_generic_argument,
    pydantic_generic_origin,
    strip_annotated,
)
from .base import BaseState, Command
from .errors import ProcessorDefinitionError
from .outcomes import Applied, Delete, Ignored, Rejected, Retry
from .schema import CommandDefinition, FSMSchema, StateModel

type Function = Callable[..., Any]


class MatchMode(StrEnum):
    EXACT = "exact"
    SUBCLASSES = "subclasses"


class ProducerKind(StrEnum):
    REACTION = "reaction"
    COMMAND = "command"
    INITIALIZER = "initializer"


@dataclass(frozen=True, slots=True)
class ReactionDefinition:
    name: str
    function: Function
    match: MatchMode
    sources: frozenset[StateModel]
    targets: frozenset[StateModel]
    can_delete: bool
    can_retry: bool
    can_leave_unchanged: bool

    @property
    def transitioning(self) -> bool:
        return bool(self.targets) or self.can_delete


@dataclass(frozen=True, slots=True)
class CommandHandlerDefinition:
    name: str
    function: Function
    match: MatchMode
    command: CommandDefinition
    sources: frozenset[StateModel]
    targets: frozenset[StateModel]
    can_delete: bool
    can_retry: bool


@dataclass(frozen=True, slots=True)
class InitializerDefinition:
    name: str
    function: Function
    command: CommandDefinition
    targets: frozenset[StateModel]
    can_retry: bool


@dataclass(frozen=True, slots=True)
class GraphEdge:
    producer_kind: ProducerKind
    processor: str
    function: str
    source: StateModel | None
    target: StateModel | None
    deletes: bool = False


@dataclass(frozen=True, slots=True)
class ComposedGraph:
    schema: FSMSchema[Any]
    processors: tuple["FSMProcessor", ...]
    edges: frozenset[GraphEdge]


@dataclass(frozen=True, slots=True)
class _PendingFunction:
    kind: ProducerKind
    function: Function
    name: str | None
    match: MatchMode


@dataclass(frozen=True, slots=True)
class _ReturnDefinition:
    targets: frozenset[StateModel]
    can_delete: bool
    can_retry: bool
    can_leave_unchanged: bool


class FSMProcessor:
    def __init__(self, *, schema: FSMSchema[Any], name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ProcessorDefinitionError("processor name must be a non-empty string")
        if "/" in name:
            raise ProcessorDefinitionError("processor name must not contain '/'")

        self.schema = schema
        self.name = name
        self._pending: list[_PendingFunction] = []
        self._reactions: tuple[ReactionDefinition, ...] | None = None
        self._commands: Mapping[type[Command[Any]], CommandHandlerDefinition] | None = None
        self._initializers: Mapping[type[Command[Any]], InitializerDefinition] | None = None

    @overload
    def reaction[F: Function](
        self,
        function: F,
        /,
        *,
        match: MatchMode = MatchMode.EXACT,
        name: str | None = None,
    ) -> F: ...

    @overload
    def reaction[F: Function](
        self,
        function: None = None,
        /,
        *,
        match: MatchMode = MatchMode.EXACT,
        name: str | None = None,
    ) -> Callable[[F], F]: ...

    def reaction[F: Function](
        self,
        function: F | None = None,
        /,
        *,
        match: MatchMode = MatchMode.EXACT,
        name: str | None = None,
    ) -> F | Callable[[F], F]:
        return self._decorator(
            kind=ProducerKind.REACTION,
            function=function,
            name=name,
            match=match,
        )

    @overload
    def command[F: Function](
        self,
        function: F,
        /,
        *,
        match: MatchMode = MatchMode.EXACT,
        name: str | None = None,
    ) -> F: ...

    @overload
    def command[F: Function](
        self,
        function: None = None,
        /,
        *,
        match: MatchMode = MatchMode.EXACT,
        name: str | None = None,
    ) -> Callable[[F], F]: ...

    def command[F: Function](
        self,
        function: F | None = None,
        /,
        *,
        match: MatchMode = MatchMode.EXACT,
        name: str | None = None,
    ) -> F | Callable[[F], F]:
        return self._decorator(
            kind=ProducerKind.COMMAND,
            function=function,
            name=name,
            match=match,
        )

    @overload
    def initializer[F: Function](
        self,
        function: F,
        /,
        *,
        name: str | None = None,
    ) -> F: ...

    @overload
    def initializer[F: Function](
        self,
        function: None = None,
        /,
        *,
        name: str | None = None,
    ) -> Callable[[F], F]: ...

    def initializer[F: Function](
        self,
        function: F | None = None,
        /,
        *,
        name: str | None = None,
    ) -> F | Callable[[F], F]:
        return self._decorator(
            kind=ProducerKind.INITIALIZER,
            function=function,
            name=name,
            match=MatchMode.EXACT,
        )

    @property
    def reactions(self) -> tuple[ReactionDefinition, ...]:
        self._require_finalized()
        assert self._reactions is not None
        return self._reactions

    @property
    def commands(self) -> Mapping[type[Command[Any]], CommandHandlerDefinition]:
        self._require_finalized()
        assert self._commands is not None
        return self._commands

    @property
    def initializers(self) -> Mapping[type[Command[Any]], InitializerDefinition]:
        self._require_finalized()
        assert self._initializers is not None
        return self._initializers

    def finalize(self) -> "FSMProcessor":
        if self._reactions is not None:
            return self

        reactions: list[ReactionDefinition] = []
        commands: dict[type[Command[Any]], CommandHandlerDefinition] = {}
        initializers: dict[type[Command[Any]], InitializerDefinition] = {}
        names: set[str] = set()

        for pending in self._pending:
            name = pending.name or _default_function_name(pending.function)
            if not name or "/" in name:
                raise ProcessorDefinitionError(
                    f"function name must be non-empty and must not contain '/': {name!r}"
                )
            if name in names:
                raise ProcessorDefinitionError(
                    f"processor {self.name!r} registers function name {name!r} more than once"
                )
            names.add(name)

            if pending.kind is ProducerKind.REACTION:
                reactions.append(self._compile_reaction(pending, name))
            elif pending.kind is ProducerKind.COMMAND:
                definition = self._compile_command(pending, name)
                if definition.command.model in commands or definition.command.model in initializers:
                    raise ProcessorDefinitionError(
                        f"command {definition.command.model.__qualname__} has more than "
                        f"one function in processor {self.name!r}"
                    )
                commands[definition.command.model] = definition
            else:
                definition = self._compile_initializer(pending, name)
                if definition.command.model in commands or definition.command.model in initializers:
                    raise ProcessorDefinitionError(
                        f"command {definition.command.model.__qualname__} has more than "
                        f"one function in processor {self.name!r}"
                    )
                initializers[definition.command.model] = definition

        _validate_transitioning_reactions(reactions, scope=f"processor {self.name!r}")

        self._reactions = tuple(reactions)
        self._commands = MappingProxyType(commands)
        self._initializers = MappingProxyType(initializers)
        return self

    def _decorator[F: Function](
        self,
        *,
        kind: ProducerKind,
        function: F | None,
        name: str | None,
        match: MatchMode,
    ) -> F | Callable[[F], F]:
        if self._reactions is not None:
            raise ProcessorDefinitionError(f"processor {self.name!r} is already finalized")
        if not isinstance(match, MatchMode):
            raise ProcessorDefinitionError(f"unsupported match mode: {match!r}")

        def register(candidate: F) -> F:
            self._pending.append(
                _PendingFunction(
                    kind=kind,
                    function=candidate,
                    name=name,
                    match=match,
                )
            )
            return candidate

        if function is None:
            return register
        return register(function)

    def _compile_reaction(
        self,
        pending: _PendingFunction,
        name: str,
    ) -> ReactionDefinition:
        parameters, return_annotation = _resolved_signature(pending.function)
        if len(parameters) != 1:
            raise ProcessorDefinitionError(
                f"reaction {name!r} must accept exactly one State parameter"
            )

        sources = _expand_state_annotation(
            schema=self.schema,
            annotation=parameters[0],
            match=pending.match,
            context=f"reaction {name!r} parameter",
        )
        returns = _reaction_return(
            schema=self.schema,
            annotation=return_annotation,
            context=f"reaction {name!r} return",
        )

        return ReactionDefinition(
            name=name,
            function=pending.function,
            match=pending.match,
            sources=sources,
            targets=returns.targets,
            can_delete=returns.can_delete,
            can_retry=returns.can_retry,
            can_leave_unchanged=returns.can_leave_unchanged,
        )

    def _compile_command(
        self,
        pending: _PendingFunction,
        name: str,
    ) -> CommandHandlerDefinition:
        parameters, return_annotation = _resolved_signature(pending.function)
        command, state_annotation = _command_parameters(
            schema=self.schema,
            parameters=parameters,
            context=f"command function {name!r}",
        )
        sources = _expand_state_annotation(
            schema=self.schema,
            annotation=state_annotation,
            match=pending.match,
            context=f"command function {name!r} State parameter",
        )
        returns = _command_return(
            schema=self.schema,
            command=command,
            annotation=return_annotation,
            context=f"command function {name!r} return",
        )

        return CommandHandlerDefinition(
            name=name,
            function=pending.function,
            match=pending.match,
            command=command,
            sources=sources,
            targets=returns.targets,
            can_delete=returns.can_delete,
            can_retry=returns.can_retry,
        )

    def _compile_initializer(
        self,
        pending: _PendingFunction,
        name: str,
    ) -> InitializerDefinition:
        parameters, return_annotation = _resolved_signature(pending.function)
        if len(parameters) != 1:
            raise ProcessorDefinitionError(
                f"initializer {name!r} must accept exactly one Command parameter"
            )
        command_model = strip_annotated(parameters[0])
        if not _is_command_model(command_model):
            raise ProcessorDefinitionError(
                f"initializer {name!r} parameter must be one registered Command Model"
            )
        try:
            command = self.schema.command_definition(command_model)
        except Exception as error:
            raise ProcessorDefinitionError(str(error)) from error

        returns = _command_return(
            schema=self.schema,
            command=command,
            annotation=return_annotation,
            context=f"initializer {name!r} return",
        )
        if not returns.targets:
            raise ProcessorDefinitionError(
                f"initializer {name!r} must return Applied[StateModel] on some path"
            )
        if returns.can_delete:
            raise ProcessorDefinitionError(f"initializer {name!r} cannot complete with Delete")

        return InitializerDefinition(
            name=name,
            function=pending.function,
            command=command,
            targets=returns.targets,
            can_retry=returns.can_retry,
        )

    def _require_finalized(self) -> None:
        if self._reactions is None:
            raise ProcessorDefinitionError(f"processor {self.name!r} has not been finalized")


def compose_processors(*processors: FSMProcessor) -> ComposedGraph:
    if not processors:
        raise ProcessorDefinitionError("at least one processor is required")

    for processor in processors:
        processor.finalize()

    schema = processors[0].schema
    if any(processor.schema is not schema for processor in processors[1:]):
        raise ProcessorDefinitionError("cannot compose processors from different schemas")
    names = [processor.name for processor in processors]
    if len(names) != len(set(names)):
        raise ProcessorDefinitionError("processor names must be unique in a Composed Graph")

    reactions = [reaction for processor in processors for reaction in processor.reactions]
    _validate_transitioning_reactions(reactions, scope="Composed Graph")

    command_owners: dict[type[Command[Any]], str] = {}
    for processor in processors:
        for command in (*processor.commands, *processor.initializers):
            owner = command_owners.get(command)
            if owner is not None:
                raise ProcessorDefinitionError(
                    f"command {command.__qualname__} is implemented by both "
                    f"{owner!r} and {processor.name!r}"
                )
            command_owners[command] = processor.name

    edges: set[GraphEdge] = set()
    for processor in processors:
        edges.update(_processor_edges(processor))

    return ComposedGraph(
        schema=schema,
        processors=processors,
        edges=frozenset(edges),
    )


def _resolved_signature(function: Function) -> tuple[tuple[Any, ...], Any]:
    if not inspect.iscoroutinefunction(function):
        raise ProcessorDefinitionError(
            f"{_default_function_name(function)!r} must be declared with async def"
        )

    signature = inspect.signature(function)
    try:
        hints = get_type_hints(function, include_extras=True)
    except (NameError, TypeError) as error:
        raise ProcessorDefinitionError(
            f"cannot resolve annotations for {_default_function_name(function)!r}: {error}"
        ) from error

    parameters: list[Any] = []
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise ProcessorDefinitionError(
                f"{_default_function_name(function)!r} cannot use *args or **kwargs"
            )
        if parameter.name not in hints:
            raise ProcessorDefinitionError(
                f"parameter {parameter.name!r} of "
                f"{_default_function_name(function)!r} must be annotated"
            )
        if hints[parameter.name] is Any:
            raise ProcessorDefinitionError(
                f"parameter {parameter.name!r} of "
                f"{_default_function_name(function)!r} cannot be Any"
            )
        parameters.append(hints[parameter.name])

    if "return" not in hints:
        raise ProcessorDefinitionError(
            f"{_default_function_name(function)!r} must have a return annotation"
        )
    if hints["return"] is Any:
        raise ProcessorDefinitionError(f"{_default_function_name(function)!r} return cannot be Any")

    return tuple(parameters), hints["return"]


def _expand_state_annotation(
    *,
    schema: FSMSchema[Any],
    annotation: Any,
    match: MatchMode,
    context: str,
) -> frozenset[StateModel]:
    declared = flatten_union(annotation)
    if not declared:
        raise ProcessorDefinitionError(f"{context} does not declare a State Model")

    sources: set[StateModel] = set()
    for member in declared:
        member = strip_annotated(member)
        if not _is_state_model(member):
            raise ProcessorDefinitionError(
                f"{context} contains unsupported type {member!r}; expected State Models"
            )

        if match is MatchMode.EXACT:
            if not schema.contains_state_model(member):
                raise ProcessorDefinitionError(
                    f"{context} uses unregistered or non-concrete State Model "
                    f"{member.__qualname__}; use SUBCLASSES for a registered hierarchy"
                )
            sources.add(member)
            continue

        _validate_state_binding(schema, member, context)
        origin = pydantic_generic_origin(member)
        matched = {concrete for concrete in schema.states.values() if issubclass(concrete, origin)}
        if not matched:
            raise ProcessorDefinitionError(f"{context} does not match any concrete State Model")
        sources.update(matched)

    return frozenset(sources)


def _validate_state_binding(
    schema: FSMSchema[Any],
    model: StateModel,
    context: str,
) -> None:
    try:
        binding = pydantic_generic_argument(model, BaseState)
    except (LookupError, TypeError) as error:
        raise ProcessorDefinitionError(
            f"{context} has an unresolved BaseState specialization"
        ) from error

    if isinstance(binding, TypeVar) or binding is Any:
        raise ProcessorDefinitionError(
            f"{context} must specialize its state enum before SUBCLASSES matching"
        )
    if binding is schema.state_enum:
        return
    if get_origin(binding) is Literal:
        members = get_args(binding)
        if members and all(type(member) is schema.state_enum for member in members):
            return
    raise ProcessorDefinitionError(
        f"{context} is specialized for a different state enum: {binding!r}"
    )


def _reaction_return(
    *,
    schema: FSMSchema[Any],
    annotation: Any,
    context: str,
) -> _ReturnDefinition:
    targets: set[StateModel] = set()
    can_delete = False
    can_retry = False
    can_leave_unchanged = False

    for member in flatten_union(annotation):
        if member is type(None):
            can_leave_unchanged = True
        elif member is Retry:
            can_retry = True
        elif member is Delete:
            can_delete = True
        elif _is_state_model(member):
            if not schema.contains_state_model(member):
                raise ProcessorDefinitionError(
                    f"{context} contains unregistered State Model {member.__qualname__}"
                )
            targets.add(member)
        else:
            raise ProcessorDefinitionError(f"{context} contains unsupported outcome {member!r}")

    if not (targets or can_delete or can_retry or can_leave_unchanged):
        raise ProcessorDefinitionError(f"{context} contains no outcomes")

    return _ReturnDefinition(
        targets=frozenset(targets),
        can_delete=can_delete,
        can_retry=can_retry,
        can_leave_unchanged=can_leave_unchanged,
    )


def _command_parameters(
    *,
    schema: FSMSchema[Any],
    parameters: tuple[Any, ...],
    context: str,
) -> tuple[CommandDefinition, Any]:
    if len(parameters) != 2:
        raise ProcessorDefinitionError(f"{context} must accept one Command and one State parameter")

    command_annotations = [
        strip_annotated(annotation)
        for annotation in parameters
        if _is_command_model(strip_annotated(annotation))
    ]
    state_annotations = [
        annotation
        for annotation in parameters
        if all(_is_state_model(member) for member in flatten_union(annotation))
    ]
    if len(command_annotations) != 1 or len(state_annotations) != 1:
        raise ProcessorDefinitionError(
            f"{context} must accept exactly one Command Model and one State annotation"
        )

    try:
        command = schema.command_definition(command_annotations[0])
    except Exception as error:
        raise ProcessorDefinitionError(str(error)) from error
    return command, state_annotations[0]


def _command_return(
    *,
    schema: FSMSchema[Any],
    command: CommandDefinition,
    annotation: Any,
    context: str,
) -> _ReturnDefinition:
    expected = frozenset(flatten_union(command.result_type))
    actual_members = frozenset(flatten_union(annotation))
    can_retry = Retry in actual_members
    completed = actual_members.difference({Retry})

    if completed != expected:
        raise ProcessorDefinitionError(
            f"{context} must equal {command.model.__qualname__}'s declared result "
            f"type plus optional Retry"
        )

    targets: set[StateModel] = set()
    can_delete = False
    for member in expected:
        origin = get_origin(member)
        if origin is Applied:
            arguments = get_args(member)
            if len(arguments) != 1 or not _is_state_model(arguments[0]):
                raise ProcessorDefinitionError(
                    f"{command.model.__qualname__} result contains invalid Applied target"
                )
            target = arguments[0]
            if not schema.contains_state_model(target):
                raise ProcessorDefinitionError(
                    f"{command.model.__qualname__} result contains unregistered "
                    f"State Model {target.__qualname__}"
                )
            targets.add(target)
        elif origin is Rejected:
            arguments = get_args(member)
            if (
                len(arguments) != 1
                or not isinstance(arguments[0], type)
                or not issubclass(arguments[0], BaseModel)
            ):
                raise ProcessorDefinitionError(
                    f"{command.model.__qualname__} result contains invalid Rejected error type"
                )
        elif member is Ignored:
            continue
        elif member is Delete:
            can_delete = True
        else:
            raise ProcessorDefinitionError(
                f"{command.model.__qualname__} result contains unsupported member {member!r}"
            )

    return _ReturnDefinition(
        targets=frozenset(targets),
        can_delete=can_delete,
        can_retry=can_retry,
        can_leave_unchanged=False,
    )


def _processor_edges(processor: FSMProcessor) -> Iterable[GraphEdge]:
    for reaction in processor.reactions:
        for source in reaction.sources:
            for target in reaction.targets:
                yield GraphEdge(
                    producer_kind=ProducerKind.REACTION,
                    processor=processor.name,
                    function=reaction.name,
                    source=source,
                    target=target,
                )
            if reaction.can_delete:
                yield GraphEdge(
                    producer_kind=ProducerKind.REACTION,
                    processor=processor.name,
                    function=reaction.name,
                    source=source,
                    target=None,
                    deletes=True,
                )

    for definition in processor.commands.values():
        for source in definition.sources:
            for target in definition.targets:
                yield GraphEdge(
                    producer_kind=ProducerKind.COMMAND,
                    processor=processor.name,
                    function=definition.name,
                    source=source,
                    target=target,
                )
            if definition.can_delete:
                yield GraphEdge(
                    producer_kind=ProducerKind.COMMAND,
                    processor=processor.name,
                    function=definition.name,
                    source=source,
                    target=None,
                    deletes=True,
                )

    for definition in processor.initializers.values():
        for target in definition.targets:
            yield GraphEdge(
                producer_kind=ProducerKind.INITIALIZER,
                processor=processor.name,
                function=definition.name,
                source=None,
                target=target,
            )


def _validate_transitioning_reactions(
    reactions: Iterable[ReactionDefinition],
    *,
    scope: str,
) -> None:
    owners: dict[StateModel, str] = {}
    for reaction in reactions:
        if not reaction.transitioning:
            continue
        for source in reaction.sources:
            owner = owners.get(source)
            if owner is not None:
                raise ProcessorDefinitionError(
                    f"{scope} has transitioning Reactions {owner!r} and "
                    f"{reaction.name!r} for {source.__qualname__}"
                )
            owners[source] = reaction.name


def _is_state_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseState)


def _is_command_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, Command)


def _default_function_name(function: Function) -> str:
    return f"{function.__module__}.{function.__qualname__}"
