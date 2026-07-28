# Declare state discriminators as literal fields

Concrete State Models declare their discriminator as an explicit enum `Literal` field with the same default value. A domain base remains generic over its enum subtype, so specializing it with the concrete `Literal` is accepted by static type checkers while a member of another enum violates the bound. This uses standard Pydantic discriminators and avoids metaclass or internal-field mutation.

```python
class OrderState[S: OrderStatus](BaseState[S]):
    ...

class Created(OrderState[Literal[OrderStatus.CREATED]]):
    state: Literal[OrderStatus.CREATED] = OrderStatus.CREATED
```

The FSM Schema also validates at runtime that each registered model has one literal discriminator, its default matches that literal, it belongs to the schema's enum, and no discriminator is registered twice. When finalized, the concrete models must form a complete one-to-one mapping with every member of the schema's enum; missing members are startup errors. Intermediate generic base models are not part of this mapping.
