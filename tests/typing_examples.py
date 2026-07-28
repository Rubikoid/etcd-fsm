from collections.abc import Callable, Coroutine
from typing import Any, Literal, assert_type

from etcd_fsm import FSMProcessor

from .order_domain import ORDER_SCHEMA, Created, OrderStatus

processor = FSMProcessor(schema=ORDER_SCHEMA, name="typing")


async def observer(state: Created) -> None:
    _ = state


decorated = processor.reaction()(observer)
typed_observer: Callable[[Created], Coroutine[Any, Any, None]] = decorated
_ = typed_observer


def _check_state_type(created: Created) -> None:
    assert_type(created.state, Literal[OrderStatus.CREATED])


_ = _check_state_type
