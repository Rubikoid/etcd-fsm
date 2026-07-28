from decimal import Decimal
from enum import StrEnum, auto
from typing import Literal
from uuid import UUID

from etcd_fsm import (
    Applied,
    BaseState,
    Command,
    Delete,
    FrozenModel,
    FSMProcessor,
    FSMSchema,
    Ignored,
    MatchMode,
    Rejected,
    Retry,
)


class OrderStatus(StrEnum):
    CREATED = auto()
    PAYMENT_REQUIRED = auto()
    PAID = auto()
    PAYMENT_FAILED = auto()
    HANDED_TO_DELIVERY = auto()
    DELIVERED = auto()
    CANCELLED = auto()


class OrderCommand(StrEnum):
    CREATE = auto()
    CANCEL = auto()
    DELETE = auto()


class OrderState[S: OrderStatus](BaseState[S]):
    account_id: UUID
    order_id: UUID

    def fsm_key(self) -> str:
        return f"{self.account_id}/{self.order_id}"


class Created(OrderState[Literal[OrderStatus.CREATED]]):
    state: Literal[OrderStatus.CREATED] = OrderStatus.CREATED
    total: Decimal


class PaymentRequired(OrderState[Literal[OrderStatus.PAYMENT_REQUIRED]]):
    state: Literal[OrderStatus.PAYMENT_REQUIRED] = OrderStatus.PAYMENT_REQUIRED
    total: Decimal
    attempt: int = 1


class Paid(OrderState[Literal[OrderStatus.PAID]]):
    state: Literal[OrderStatus.PAID] = OrderStatus.PAID
    payment_id: UUID


class PaymentFailed(OrderState[Literal[OrderStatus.PAYMENT_FAILED]]):
    state: Literal[OrderStatus.PAYMENT_FAILED] = OrderStatus.PAYMENT_FAILED
    total: Decimal
    attempt: int
    reason: str


class HandedToDelivery(OrderState[Literal[OrderStatus.HANDED_TO_DELIVERY]]):
    state: Literal[OrderStatus.HANDED_TO_DELIVERY] = OrderStatus.HANDED_TO_DELIVERY
    delivery_id: UUID


class Delivered(OrderState[Literal[OrderStatus.DELIVERED]]):
    state: Literal[OrderStatus.DELIVERED] = OrderStatus.DELIVERED
    delivery_id: UUID


class Cancelled(OrderState[Literal[OrderStatus.CANCELLED]]):
    state: Literal[OrderStatus.CANCELLED] = OrderStatus.CANCELLED
    reason: str


class OrderAlreadyExists(FrozenModel):
    message: str


class CannotCancelOrder(FrozenModel):
    current_state: OrderStatus


class CannotDeleteOrder(FrozenModel):
    current_state: OrderStatus


type CreateOrderResult = Applied[Created] | Rejected[OrderAlreadyExists]
type CancelOrderResult = Applied[Cancelled] | Rejected[CannotCancelOrder] | Ignored
type DeleteOrderResult = Delete | Rejected[CannotDeleteOrder]


class CreateOrder(Command[CreateOrderResult]):
    command: Literal[OrderCommand.CREATE] = OrderCommand.CREATE
    account_id: UUID
    order_id: UUID
    total: Decimal

    def fsm_key(self) -> str:
        return f"{self.account_id}/{self.order_id}"


class CancelOrder(Command[CancelOrderResult]):
    command: Literal[OrderCommand.CANCEL] = OrderCommand.CANCEL
    account_id: UUID
    order_id: UUID
    reason: str

    def fsm_key(self) -> str:
        return f"{self.account_id}/{self.order_id}"


class DeleteOrder(Command[DeleteOrderResult]):
    command: Literal[OrderCommand.DELETE] = OrderCommand.DELETE
    account_id: UUID
    order_id: UUID

    def fsm_key(self) -> str:
        return f"{self.account_id}/{self.order_id}"


ORDER_SCHEMA = FSMSchema(
    name="orders",
    namespace="/state/orders",
    state_enum=OrderStatus,
    states=(
        Created,
        PaymentRequired,
        Paid,
        PaymentFailed,
        HandedToDelivery,
        Delivered,
        Cancelled,
    ),
    command_enum=OrderCommand,
    commands=(CreateOrder, CancelOrder, DeleteOrder),
)


PAYMENTS = FSMProcessor(schema=ORDER_SCHEMA, name="payments")
DELIVERY = FSMProcessor(schema=ORDER_SCHEMA, name="delivery")


@PAYMENTS.initializer()
async def create_order(command: CreateOrder) -> CreateOrderResult | Retry:
    return Applied(
        Created(
            account_id=command.account_id,
            order_id=command.order_id,
            total=command.total,
        )
    )


@PAYMENTS.reaction
async def request_payment(state: Created) -> PaymentRequired:
    return PaymentRequired(
        account_id=state.account_id,
        order_id=state.order_id,
        total=state.total,
    )


@PAYMENTS.reaction()
async def charge_payment(
    state: PaymentRequired,
) -> Paid | PaymentFailed | Retry:
    return Retry()


@PAYMENTS.reaction()
async def retry_payment(state: PaymentFailed) -> PaymentRequired | Retry:
    return Retry()


@PAYMENTS.command()
async def cancel_order(
    command: CancelOrder,
    state: Created | PaymentRequired | PaymentFailed,
) -> CancelOrderResult | Retry:
    return Applied(
        Cancelled(
            account_id=state.account_id,
            order_id=state.order_id,
            reason=command.reason,
        )
    )


@PAYMENTS.command(match=MatchMode.SUBCLASSES)
async def delete_order(
    command: DeleteOrder,
    state: OrderState[OrderStatus],
) -> DeleteOrderResult | Retry:
    return Delete()


@DELIVERY.reaction()
async def hand_to_delivery(state: Paid) -> HandedToDelivery:
    raise NotImplementedError


@DELIVERY.reaction()
async def mark_delivered(state: HandedToDelivery) -> Delivered | Retry:
    return Retry()


@DELIVERY.reaction(match=MatchMode.SUBCLASSES)
async def audit_order(state: OrderState[OrderStatus]) -> Retry | None:
    return None
