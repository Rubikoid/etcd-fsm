import pytest

from etcd_fsm import (
    FSMProcessor,
    MatchMode,
    ProcessorDefinitionError,
    ProducerKind,
    compose_processors,
)

from .order_domain import (
    DELIVERY,
    ORDER_SCHEMA,
    PAYMENTS,
    Cancelled,
    Created,
    Delivered,
    OrderState,
    OrderStatus,
    Paid,
    PaymentRequired,
)


def test_processors_compile_signature_derived_graph() -> None:
    graph = compose_processors(PAYMENTS, DELIVERY)

    transitions = {
        (edge.producer_kind, edge.source, edge.target, edge.deletes) for edge in graph.edges
    }
    assert (ProducerKind.REACTION, Created, PaymentRequired, False) in transitions
    assert (ProducerKind.REACTION, Paid, None, False) not in transitions
    assert any(edge.source is Paid and edge.target is not None for edge in graph.edges)
    assert any(
        edge.producer_kind is ProducerKind.INITIALIZER
        and edge.source is None
        and edge.target is Created
        for edge in graph.edges
    )
    assert any(
        edge.producer_kind is ProducerKind.COMMAND
        and edge.source is Created
        and edge.target is Cancelled
        for edge in graph.edges
    )
    assert any(
        edge.producer_kind is ProducerKind.COMMAND and edge.source is Delivered and edge.deletes
        for edge in graph.edges
    )


def test_inherited_observer_expands_to_every_registered_state() -> None:
    DELIVERY.finalize()
    audit = next(
        reaction for reaction in DELIVERY.reactions if reaction.function.__name__ == "audit_order"
    )

    assert audit.match is MatchMode.SUBCLASSES
    assert audit.sources == frozenset(ORDER_SCHEMA.states.values())
    assert not audit.transitioning


def test_decorator_preserves_original_function() -> None:
    processor = FSMProcessor(schema=ORDER_SCHEMA, name="identity")

    async def observer(state: Created) -> None:
        return None

    assert processor.reaction(observer) is observer


def test_overlapping_transitioning_reactions_are_rejected() -> None:
    processor = FSMProcessor(schema=ORDER_SCHEMA, name="overlap")

    @processor.reaction(match=MatchMode.SUBCLASSES)
    async def _broad(state: OrderState[OrderStatus]) -> Cancelled:
        raise NotImplementedError

    @processor.reaction()
    async def _exact(state: Created) -> PaymentRequired:
        raise NotImplementedError

    _ = _broad, _exact

    try:
        processor.finalize()
    except ProcessorDefinitionError as error:
        assert "transitioning Reactions" in str(error)
    else:
        raise AssertionError("overlapping transitioning reactions were accepted")


def test_sync_reaction_is_rejected_during_finalization() -> None:
    processor = FSMProcessor(schema=ORDER_SCHEMA, name="sync")

    @processor.reaction()
    def _invalid(state: Created) -> None:
        return None

    _ = _invalid

    try:
        processor.finalize()
    except ProcessorDefinitionError as error:
        assert "async def" in str(error)
    else:
        raise AssertionError("synchronous reaction was accepted")


def test_composed_graph_rejects_duplicate_processor_names() -> None:
    first = FSMProcessor(schema=ORDER_SCHEMA, name="duplicate")
    second = FSMProcessor(schema=ORDER_SCHEMA, name="duplicate")

    with pytest.raises(ProcessorDefinitionError, match="names must be unique"):
        compose_processors(first, second)


def test_composed_graph_rejects_transition_overlap_across_processors() -> None:
    first = FSMProcessor(schema=ORDER_SCHEMA, name="first")
    second = FSMProcessor(schema=ORDER_SCHEMA, name="second")

    @first.reaction()
    async def _first(state: Created) -> PaymentRequired:
        raise NotImplementedError

    @second.reaction()
    async def _second(state: Created) -> Cancelled:
        raise NotImplementedError

    _ = _first, _second

    with pytest.raises(ProcessorDefinitionError, match="transitioning Reactions"):
        compose_processors(first, second)
