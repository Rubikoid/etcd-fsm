import sys
from pathlib import Path
from typing import Annotated

from cyclopts import App, Parameter

from ._graph import GraphFormat, GraphImportError, import_definitions, render_graph
from .errors import DefinitionError

app = App(
    name="etcd-fsm",
    help="Inspect etcd-fsm declarations.",
)


@app.command
def graph(
    references: list[str],
    *,
    format: GraphFormat = "mermaid",
    output: Annotated[Path | None, Parameter(name=["--output", "-o"])] = None,
) -> None:
    """Render transitions discovered in imported schemas and processors.

    Parameters
    ----------
    references
        Python MODULE, FILE.py, or either:OBJECT references to import.
    format
        Output Mermaid or Graphviz DOT source.
    output
        Write the graph to this file instead of stdout.
    """
    rendered = render_graph(import_definitions(references), format=format)
    if output is None:
        print(rendered, end="")
    else:
        output.write_text(rendered, encoding="utf-8")


def main() -> None:
    try:
        app()
    except (DefinitionError, GraphImportError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
