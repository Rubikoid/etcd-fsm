from dataclasses import FrozenInstanceError
from decimal import Decimal
from typing import Annotated
from uuid import uuid4

import pytest
from pydantic import Field, TypeAdapter, ValidationError

from etcd_fsm import Applied, Delete, Ignored, InvalidFSMKey, Rejected, Retry
from etcd_fsm.base import FSMKey, validate_fsm_key

from .order_domain import (
    CannotCancelOrder,
    Created,
    OrderStatus,
    PaymentRequired,
)

type PersistedResult = Annotated[
    Applied[Created] | Rejected[CannotCancelOrder] | Ignored | Delete,
    Field(discriminator="kind"),
]


def test_state_discriminator_is_literal_with_default() -> None:
    state = Created(account_id=uuid4(), order_id=uuid4(), total=Decimal("12.50"))

    assert state.state is OrderStatus.CREATED
    assert state.model_dump()["state"] is OrderStatus.CREATED
    assert Created.model_json_schema()["properties"]["state"]["const"] == "created"


def test_wrong_state_discriminator_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Created.model_validate(
            {
                "state": OrderStatus.PAID,
                "account_id": uuid4(),
                "order_id": uuid4(),
                "total": Decimal("12.50"),
            }
        )


def test_state_is_frozen_and_structurally_keyed() -> None:
    state = PaymentRequired(
        account_id=uuid4(),
        order_id=uuid4(),
        total=Decimal("12.50"),
    )

    assert isinstance(state, FSMKey)
    with pytest.raises(ValidationError):
        state.attempt = 2


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/absolute",
        "trailing/",
        "double//slash",
        ".",
        "orders/../other",
        "orders/rpc/item",
    ],
)
def test_invalid_fsm_keys_are_rejected(key: str) -> None:
    with pytest.raises(InvalidFSMKey):
        validate_fsm_key(key)


def test_composite_fsm_key_is_allowed() -> None:
    assert validate_fsm_key("dtc/user-id/task-id") == "dtc/user-id/task-id"


def test_outcomes_are_frozen_and_distinguishable() -> None:
    state = Created(account_id=uuid4(), order_id=uuid4(), total=Decimal("1"))
    rejected = Rejected(CannotCancelOrder(current_state=OrderStatus.PAID))

    assert Applied(state).kind == "applied"
    assert rejected.kind == "rejected"
    assert Ignored().kind == "ignored"
    assert Delete().kind == "delete"
    assert Retry().kind == "retry"

    outcome = Delete()
    with pytest.raises(FrozenInstanceError):
        outcome.kind = "ignored"  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.parametrize(
    "result",
    [
        Applied(Created(account_id=uuid4(), order_id=uuid4(), total=Decimal(1))),
        Rejected(CannotCancelOrder(current_state=OrderStatus.PAID)),
        Ignored(),
        Delete(),
    ],
)
def test_command_outcomes_round_trip_with_discriminator(
    result: PersistedResult,
) -> None:
    adapter = TypeAdapter[PersistedResult](PersistedResult)

    decoded = adapter.validate_json(adapter.dump_json(result))

    assert decoded == result
