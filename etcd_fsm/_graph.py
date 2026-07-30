import hashlib
import html
import importlib
import importlib.util
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, cast

from .processor import ComposedGraph, FSMProcessor, GraphEdge, compose_processors
from .schema import FSMSchema, StateModel

type GraphFormat = Literal["mermaid", "dot"]


class GraphImportError(ValueError):
    """A graph import reference is invalid or contains no FSM definitions."""


@dataclass(frozen=True, slots=True)
class ImportedDefinitions:
    schemas: tuple[FSMSchema[Any], ...]
    processors: tuple[FSMProcessor, ...]


def import_definitions(references: Sequence[str]) -> ImportedDefinitions:
    if not references:
        raise GraphImportError(
            "at least one module, file, or module-or-file:object reference is required"
        )

    schemas: list[FSMSchema[Any]] = []
    processors: list[FSMProcessor] = []
    seen_schemas: set[int] = set()
    seen_processors: set[int] = set()
    seen_collections: set[int] = set()

    def collect(value: object) -> None:
        if isinstance(value, FSMProcessor):
            identity = id(value)
            if identity not in seen_processors:
                seen_processors.add(identity)
                processors.append(value)
            collect(value.schema)
            return
        if isinstance(value, FSMSchema):
            schema = cast(FSMSchema[Any], value)
            identity = id(schema)
            if identity not in seen_schemas:
                seen_schemas.add(identity)
                schemas.append(schema)
            return
        if isinstance(value, (tuple, list, set, frozenset)):
            collection = cast(Iterable[object], value)
            identity = id(collection)
            if identity in seen_collections:
                return
            seen_collections.add(identity)
            for member in collection:
                collect(member)

    for reference in references:
        module, value = _resolve_reference(reference)
        if value is module:
            for candidate in vars(module).values():
                collect(candidate)
        else:
            collect(value)

    if not schemas and not processors:
        joined = ", ".join(repr(reference) for reference in references)
        raise GraphImportError(f"no FSMSchema or FSMProcessor objects found in {joined}")

    return ImportedDefinitions(schemas=tuple(schemas), processors=tuple(processors))


def render_graph(
    definitions: ImportedDefinitions,
    *,
    format: GraphFormat = "mermaid",
) -> str:
    graphs = _compose_graphs(definitions)
    if format == "mermaid":
        return _render_mermaid(graphs)
    if format == "dot":
        return _render_dot(graphs)
    raise ValueError(f"unsupported graph format: {format!r}")


def _resolve_reference(reference: str) -> tuple[ModuleType, object]:
    if not reference:
        raise GraphImportError("empty import reference")

    source, separator, object_path = reference.partition(":")
    if not source or (separator and not object_path):
        raise GraphImportError(
            f"invalid import reference {reference!r}; expected MODULE, FILE.py, or either:OBJECT"
        )

    module = _import_source(source)

    if not separator:
        return module, module

    value: object = module
    for segment in object_path.split("."):
        if not segment:
            raise GraphImportError(
                f"invalid object path in {reference!r}; expected MODULE:OBJECT or FILE.py:OBJECT"
            )
        try:
            value = getattr(value, segment)
        except AttributeError as error:
            raise GraphImportError(f"{reference!r} does not resolve to an object") from error
    return module, value


def _import_source(source: str) -> ModuleType:
    if "/" in source or "\\" in source or source.endswith(".py"):
        return _import_file(Path(source))

    try:
        return importlib.import_module(source)
    except Exception as error:
        raise GraphImportError(f"cannot import module {source!r}: {error}") from error


def _import_file(path: Path) -> ModuleType:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise GraphImportError(f"Python file does not exist: {path}")
    if resolved.suffix != ".py":
        raise GraphImportError(f"import file must have a .py suffix: {path}")

    import_name = _import_name_for_file(resolved)
    if import_name is not None:
        try:
            return importlib.import_module(import_name)
        except Exception as error:
            raise GraphImportError(f"cannot import Python file {path!s}: {error}") from error

    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:16]
    module_name = f"_etcd_fsm_graph_file_{digest}"
    loaded = sys.modules.get(module_name)
    if isinstance(loaded, ModuleType):
        return loaded

    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise GraphImportError(f"cannot create an import spec for Python file {path!s}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        sys.modules.pop(module_name, None)
        raise GraphImportError(f"cannot import Python file {path!s}: {error}") from error
    return module


def _import_name_for_file(path: Path) -> str | None:
    for entry in sys.path:
        root = Path(entry or ".").resolve()
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue

        parts = relative.with_suffix("").parts
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts or any(not part.isidentifier() for part in parts):
            continue

        module_name = ".".join(parts)
        spec = importlib.util.find_spec(module_name)
        if spec is None or spec.origin is None:
            continue
        try:
            if Path(spec.origin).resolve() == path:
                return module_name
        except OSError:
            continue
    return None


def _compose_graphs(definitions: ImportedDefinitions) -> tuple[ComposedGraph, ...]:
    graphs: list[ComposedGraph] = []
    for schema in definitions.schemas:
        processors = tuple(
            processor for processor in definitions.processors if processor.schema is schema
        )
        if processors:
            graphs.append(compose_processors(*processors))
        else:
            graphs.append(ComposedGraph(schema=schema, processors=(), edges=frozenset()))
    return tuple(graphs)


def _render_mermaid(graphs: Sequence[ComposedGraph]) -> str:
    lines = ["flowchart LR"]
    for schema_index, graph in enumerate(graphs):
        prefix = f"schema_{schema_index}"
        nodes = _state_nodes(graph.schema, prefix)
        edges = _sorted_edges(graph.edges, graph.schema)
        has_initial = any(edge.source is None and not edge.deletes for edge in edges)
        has_deleted = any(edge.deletes for edge in edges)

        lines.append(f'  subgraph {prefix}["{_mermaid_text(graph.schema.name)}"]')
        if has_initial:
            lines.append(f'    {prefix}_initial(("create"))')
        if has_deleted:
            lines.append(f'    {prefix}_deleted(("deleted"))')
        for model, node_id, label in nodes:
            _ = model
            lines.append(f'    {node_id}["{_mermaid_text(label, line_break="<br/>")}"]')
        for edge in edges:
            source = f"{prefix}_initial" if edge.source is None else _node_id(nodes, edge.source)
            target = f"{prefix}_deleted" if edge.deletes else _node_id(nodes, edge.target)
            label = _edge_label(edge)
            lines.append(f'    {source} -->|"{_mermaid_text(label)}"| {target}')
        lines.append("  end")
    return "\n".join(lines) + "\n"


def _render_dot(graphs: Sequence[ComposedGraph]) -> str:
    lines = ['digraph "etcd-fsm" {', "  rankdir=LR;"]
    for schema_index, graph in enumerate(graphs):
        prefix = f"schema_{schema_index}"
        nodes = _state_nodes(graph.schema, prefix)
        edges = _sorted_edges(graph.edges, graph.schema)
        has_initial = any(edge.source is None and not edge.deletes for edge in edges)
        has_deleted = any(edge.deletes for edge in edges)

        lines.append(f'  subgraph "cluster_{prefix}" {{')
        lines.append(f'    label="{_dot_text(graph.schema.name)}";')
        if has_initial:
            lines.append(f'    "{prefix}_initial" [label="create", shape=circle];')
        if has_deleted:
            lines.append(f'    "{prefix}_deleted" [label="deleted", shape=doublecircle];')
        for model, node_id, label in nodes:
            _ = model
            lines.append(f'    "{node_id}" [label="{_dot_text(label)}", shape=box];')
        for edge in edges:
            source = f"{prefix}_initial" if edge.source is None else _node_id(nodes, edge.source)
            target = f"{prefix}_deleted" if edge.deletes else _node_id(nodes, edge.target)
            lines.append(f'    "{source}" -> "{target}" [label="{_dot_text(_edge_label(edge))}"];')
        lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _state_nodes(
    schema: FSMSchema[Any],
    prefix: str,
) -> tuple[tuple[StateModel, str, str], ...]:
    return tuple(
        (
            model,
            f"{prefix}_state_{index}",
            f"{_enum_label(discriminator)}\n{model.__qualname__}",
        )
        for index, (discriminator, model) in enumerate(schema.states.items())
    )


def _node_id(
    nodes: Sequence[tuple[StateModel, str, str]],
    model: StateModel | None,
) -> str:
    if model is None:
        raise ValueError("transition edge has neither a target nor delete semantics")
    for candidate, node_id, _ in nodes:
        if candidate is model:
            return node_id
    raise ValueError(f"state model {model.__qualname__} is not registered in the graph schema")


def _sorted_edges(
    edges: Iterable[GraphEdge],
    schema: FSMSchema[Any],
) -> tuple[GraphEdge, ...]:
    state_order = {model: index for index, model in enumerate(schema.states.values())}
    terminal = len(state_order)
    return tuple(
        sorted(
            edges,
            key=lambda edge: (
                -1 if edge.source is None else state_order[edge.source],
                terminal if edge.target is None else state_order[edge.target],
                edge.deletes,
                edge.processor,
                edge.function,
                edge.producer_kind.value,
            ),
        )
    )


def _edge_label(edge: GraphEdge) -> str:
    return f"{edge.processor}: {edge.function} [{edge.producer_kind.value}]"


def _enum_label(value: Enum) -> str:
    rendered = str(value.value)
    if rendered == value.name:
        return value.name
    return f"{value.name} ({rendered})"


def _mermaid_text(value: str, *, line_break: str = " ") -> str:
    return html.escape(value, quote=True).replace("\n", line_break)


def _dot_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
