# etcd-fsm

A typed, asynchronous distributed finite-state machine over etcd.

The current implementation contains the declarative type system, schema and
processor compiler, signature-derived transition graph, an internal etcd
contract, a deterministic MVCC fake, and the production `aetcd` adapter. The
production runtime is under construction.

```python
from enum import StrEnum, auto
from typing import Literal
from uuid import UUID

from etcd_fsm import BaseState, FSMProcessor, FSMSchema, Retry


class OrderStatus(StrEnum):
    CREATED = auto()
    PAYMENT_REQUIRED = auto()


class OrderState[S: OrderStatus](BaseState[S]):
    order_id: UUID

    def fsm_key(self) -> str:
        return str(self.order_id)


class Created(OrderState[Literal[OrderStatus.CREATED]]):
    state: Literal[OrderStatus.CREATED] = OrderStatus.CREATED


class PaymentRequired(OrderState[Literal[OrderStatus.PAYMENT_REQUIRED]]):
    state: Literal[OrderStatus.PAYMENT_REQUIRED] = OrderStatus.PAYMENT_REQUIRED
    attempt: int = 1


orders = FSMSchema(
    name="orders",
    namespace="/state/orders",
    state_enum=OrderStatus,
    states=(Created, PaymentRequired),
)
payments = FSMProcessor(schema=orders, name="payments")


@payments.reaction()
async def request_payment(state: Created) -> PaymentRequired | Retry:
    return PaymentRequired(order_id=state.order_id)


payments.finalize()
```

Function annotations are the transition definition. Concrete State Models use
literal enum discriminators, models are immutable snapshots, and the schema
rejects incomplete or inconsistent declarations before a runtime starts.

The architectural vocabulary is documented in [CONTEXT.md](./CONTEXT.md).
Design premises and implementation order are recorded in
[docs/DESIGN-NOTES.md](./docs/DESIGN-NOTES.md), with accepted decisions under
[`docs/adr/`](./docs/adr/).
