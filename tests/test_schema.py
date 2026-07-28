from collections.abc import MutableMapping
from decimal import Decimal
from enum import StrEnum, auto
from typing import Literal, cast
from uuid import uuid4

import pytest
from pydantic import ConfigDict

from etcd_fsm import BaseState, Command, FSMSchema, Ignored, SchemaDefinitionError

from .order_domain import (
    ORDER_SCHEMA,
    Created,
    CreateOrder,
    OrderCommand,
    OrderStatus,
)


def test_schema_builds_complete_immutable_registries() -> None:
    assert ORDER_SCHEMA.states[OrderStatus.CREATED] is Created
    assert ORDER_SCHEMA.commands[OrderCommand.CREATE].model is CreateOrder

    with pytest.raises(TypeError):
        cast(
            MutableMapping[OrderStatus, object],
            ORDER_SCHEMA.states,
        )[OrderStatus.CREATED] = Created


def test_schema_revalidates_state_and_key() -> None:
    state = Created(account_id=uuid4(), order_id=uuid4(), total=Decimal("3"))

    validated = ORDER_SCHEMA.validate_state_value(state)

    assert validated == state
    assert validated is not state


def test_schema_rejects_missing_state_member() -> None:
    with pytest.raises(SchemaDefinitionError, match="missing State Models"):
        FSMSchema(
            name="incomplete",
            namespace="/state/incomplete",
            state_enum=OrderStatus,
            states=(Created,),
        )


def test_schema_rejects_duplicate_discriminator() -> None:
    class Only(StrEnum):
        VALUE = auto()

    class State(BaseState[Literal[Only.VALUE]]):
        state: Literal[Only.VALUE] = Only.VALUE

        def fsm_key(self) -> str:
            return "one"

    class Duplicate(BaseState[Literal[Only.VALUE]]):
        state: Literal[Only.VALUE] = Only.VALUE

        def fsm_key(self) -> str:
            return "two"

    with pytest.raises(SchemaDefinitionError, match="declared by both"):
        FSMSchema(
            name="duplicates",
            namespace="/state/duplicates",
            state_enum=Only,
            states=(State, Duplicate),
        )


def test_schema_rejects_inherited_discriminator_without_redeclaration() -> None:
    class Only(StrEnum):
        VALUE = auto()

    class Parent(BaseState[Literal[Only.VALUE]]):
        state: Literal[Only.VALUE] = Only.VALUE

        def fsm_key(self) -> str:
            return "one"

    class Child(Parent):
        pass

    with pytest.raises(SchemaDefinitionError, match="declare 'state' locally"):
        FSMSchema(
            name="inherited",
            namespace="/state/inherited",
            state_enum=Only,
            states=(Child,),
        )


def test_schema_rejects_state_from_another_enum() -> None:
    class Expected(StrEnum):
        VALUE = auto()

    class Other(StrEnum):
        VALUE = auto()

    class Wrong(BaseState[Literal[Other.VALUE]]):
        state: Literal[Other.VALUE] = Other.VALUE

        def fsm_key(self) -> str:
            return "wrong"

    with pytest.raises(SchemaDefinitionError, match=r"expected .*Expected"):
        FSMSchema(
            name="wrong-enum",
            namespace="/state/wrong-enum",
            state_enum=Expected,
            states=(Wrong,),
        )


def test_schema_rejects_async_key_method() -> None:
    class Only(StrEnum):
        VALUE = auto()

    class AsyncKey(BaseState[Literal[Only.VALUE]]):
        state: Literal[Only.VALUE] = Only.VALUE

        async def fsm_key(self) -> str:  # pyright: ignore[reportIncompatibleMethodOverride]
            return "async"

    with pytest.raises(SchemaDefinitionError, match="must be synchronous"):
        FSMSchema(
            name="async-key",
            namespace="/state/async-key",
            state_enum=Only,
            states=(AsyncKey,),
        )


def test_schema_rejects_model_that_disables_freezing() -> None:
    class Only(StrEnum):
        VALUE = auto()

    class Mutable(BaseState[Literal[Only.VALUE]]):
        model_config = ConfigDict(frozen=False)
        state: Literal[Only.VALUE] = Only.VALUE

        def fsm_key(self) -> str:
            return "mutable"

    with pytest.raises(SchemaDefinitionError, match="must be frozen"):
        FSMSchema(
            name="mutable",
            namespace="/state/mutable",
            state_enum=Only,
            states=(Mutable,),
        )


def test_command_result_type_survives_generic_domain_base() -> None:
    class Status(StrEnum):
        READY = auto()

    class CommandType(StrEnum):
        IGNORE = auto()

    class Ready(BaseState[Literal[Status.READY]]):
        state: Literal[Status.READY] = Status.READY

        def fsm_key(self) -> str:
            return "ready"

    class DomainCommand[R](Command[R]):
        object_id: str

        def fsm_key(self) -> str:
            return self.object_id

    class Ignore(DomainCommand[Ignored]):
        command: Literal[CommandType.IGNORE] = CommandType.IGNORE

    schema = FSMSchema(
        name="generic-command",
        namespace="/state/generic-command",
        state_enum=Status,
        states=(Ready,),
        command_enum=CommandType,
        commands=(Ignore,),
    )

    assert schema.commands[CommandType.IGNORE].result_type is Ignored
