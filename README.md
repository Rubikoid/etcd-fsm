# etcd-fsm

🤖 absolute ai, 0% of code written by human 🤖

---

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

The public Python API and graph CLI are documented in Russian in
[docs/API.md](./docs/API.md).
The PyPI release process is documented in
[docs/RELEASING.md](./docs/RELEASING.md).

## Transition graph

Import modules containing schemas and processors and render their combined
transition graph as Mermaid:

```console
etcd-fsm graph my_service.orders my_service.payments
```

Python file paths are accepted as well:

```console
etcd-fsm graph tests/order_domain.py
```

Every transition is labeled with its processor, function, and producer kind.
Use explicit `module:OBJECT` or `path/to/file.py:OBJECT` references when
automatic discovery is not appropriate, or render Graphviz DOT into a file:

```console
etcd-fsm graph my_service.orders:ORDER_SCHEMA \
  my_service.payments:PAYMENTS --format dot -o orders.dot
```

The architectural vocabulary is documented in [CONTEXT.md](./CONTEXT.md).
Design premises and implementation order are recorded in
[docs/DESIGN-NOTES.md](./docs/DESIGN-NOTES.md), with accepted decisions under
[`docs/adr/`](./docs/adr/).

## Local etcd

Start an ephemeral etcd instance for integration tests:

```console
docker compose up -d --wait
```

The client endpoint is `http://127.0.0.1:12379`. Stop it and discard its data
with `docker compose down`.

Run the real-etcd integration suite explicitly:

```console
uv run pytest --run-integration -m integration
```

The integration tests use a unique `/etcd-fsm-tests/<uuid>/` prefix and clean
it after each test. Override the endpoint with `ETCD_FSM_TEST_HOST` and
`ETCD_FSM_TEST_PORT`.
