from pathlib import Path

import pytest

from etcd_fsm import FSMSchema
from etcd_fsm._graph import GraphImportError, import_definitions, render_graph
from etcd_fsm.cli import graph

from .order_domain import DELIVERY, ORDER_SCHEMA, PAYMENTS, OrderStatus

REPORTING_SCHEMA = FSMSchema(
    name="order-reporting",
    namespace="/state/order-reporting",
    state_enum=OrderStatus,
    states=ORDER_SCHEMA.states.values(),
)


def test_module_import_discovers_schema_and_all_processors() -> None:
    definitions = import_definitions(["tests.order_domain"])

    assert definitions.schemas == (ORDER_SCHEMA,)
    assert definitions.processors == (PAYMENTS, DELIVERY)


def test_file_import_discovers_schema_and_all_processors() -> None:
    definitions = import_definitions(["tests/order_domain.py"])

    assert definitions.schemas == (ORDER_SCHEMA,)
    assert definitions.processors == (PAYMENTS, DELIVERY)


def test_file_object_reference_selects_one_processor() -> None:
    definitions = import_definitions(["tests/order_domain.py:PAYMENTS"])

    assert definitions.schemas == (ORDER_SCHEMA,)
    assert definitions.processors == (PAYMENTS,)


def test_standalone_file_is_imported(tmp_path: Path) -> None:
    module = tmp_path / "fsm_definitions.py"
    module.write_text(
        "from tests.order_domain import ORDER_SCHEMA, PAYMENTS\n",
        encoding="utf-8",
    )

    definitions = import_definitions([str(module)])

    assert definitions.schemas == (ORDER_SCHEMA,)
    assert definitions.processors == (PAYMENTS,)


def test_explicit_processor_imports_add_their_schema_and_deduplicate() -> None:
    definitions = import_definitions(
        [
            "tests.order_domain:PAYMENTS",
            "tests.order_domain:DELIVERY",
            "tests.order_domain:PAYMENTS",
        ]
    )

    assert definitions.schemas == (ORDER_SCHEMA,)
    assert definitions.processors == (PAYMENTS, DELIVERY)


def test_mermaid_graph_labels_transitions_with_processor_and_function() -> None:
    rendered = render_graph(import_definitions(["tests.order_domain"]))

    assert rendered.startswith("flowchart LR\n")
    assert 'subgraph schema_0["orders"]' in rendered
    assert 'schema_0_state_0["CREATED (created)<br/>Created"]' in rendered
    assert (
        'schema_0_state_0 -->|"payments: tests.order_domain.request_payment '
        '[reaction]"| schema_0_state_1'
    ) in rendered
    assert (
        'schema_0_state_2 -->|"delivery: tests.order_domain.hand_to_delivery '
        '[reaction]"| schema_0_state_4'
    ) in rendered
    assert (
        'schema_0_initial -->|"payments: tests.order_domain.create_order '
        '[initializer]"| schema_0_state_0'
    ) in rendered
    assert (
        'schema_0_state_5 -->|"payments: tests.order_domain.delete_order '
        '[command]"| schema_0_deleted'
    ) in rendered


def test_dot_graph_contains_schema_states_and_owned_edges() -> None:
    rendered = render_graph(
        import_definitions(["tests.order_domain:PAYMENTS"]),
        format="dot",
    )

    assert rendered.startswith('digraph "etcd-fsm" {\n')
    assert 'label="orders";' in rendered
    assert 'label="CREATED (created)\\nCreated", shape=box' in rendered
    assert 'label="payments: tests.order_domain.request_payment [reaction]"' in rendered
    assert "delivery:" not in rendered


def test_multiple_schemas_render_as_separate_subgraphs() -> None:
    definitions = import_definitions(
        [
            "tests.order_domain",
            "tests.test_graph:REPORTING_SCHEMA",
        ]
    )

    rendered = render_graph(definitions)

    assert 'subgraph schema_0["orders"]' in rendered
    assert 'subgraph schema_1["order-reporting"]' in rendered


def test_graph_command_writes_requested_format(tmp_path: Path) -> None:
    output = tmp_path / "orders.dot"

    graph(["tests.order_domain"], format="dot", output=output)

    assert output.read_text(encoding="utf-8").startswith('digraph "etcd-fsm" {')


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "tests.order_domain:",
        "tests.order_domain:MISSING",
        "missing.py",
        "types",
    ],
)
def test_invalid_or_empty_import_references_are_rejected(reference: str) -> None:
    with pytest.raises(GraphImportError):
        import_definitions([reference])
