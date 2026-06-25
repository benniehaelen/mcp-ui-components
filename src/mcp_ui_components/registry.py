"""Tool + resource registry, independent of any transport.

Aggregates every component's tool/resource surface into the flat lists and dispatch
functions a host needs. Both halves of the project wire from here:

  * ``server.py``  exposes these over a real MCP server (stdio + streamable HTTP).
  * ``demo/host.py`` exposes the same functions over HTTP for the browser demo.

Keeping one source of truth means the "button click is governed exactly like a
prompt" guarantee is real: the demo host and a production host run identical code.

Each component (under :mod:`.components`) contributes four module-level names —
``TOOLS``, ``RESOURCES``, ``HANDLERS`` (tool name → callable), and ``RESOURCE_HTML``
(``ui://`` uri → HTML loader) — which are merged below. A UI-enabled tool advertises
its widget through ``_meta.ui.resourceUri`` (the SEP-1865 / MCP Apps convention);
when the host sees a tool result carrying that meta it fetches the named ``ui://``
resource and renders it sandboxed.
"""

from __future__ import annotations

from . import RESOURCE_MIME_TYPE  # re-exported for server.py
from .components import (
    join_diagram,
    lineage,
    query_plan,
    query_profile,
    trace_waterfall,
)

_COMPONENTS = [lineage, join_diagram, query_plan, query_profile, trace_waterfall]

TOOLS = [tool for c in _COMPONENTS for tool in c.TOOLS]
RESOURCES = [res for c in _COMPONENTS for res in c.RESOURCES]

_HANDLERS = {name: fn for c in _COMPONENTS for name, fn in c.HANDLERS.items()}
_RESOURCE_HTML = {uri: fn for c in _COMPONENTS for uri, fn in c.RESOURCE_HTML.items()}


def call_tool(name: str, arguments: dict | None) -> dict:
    """Execute a tool and return its JSON-serialisable result payload.

    The returned dict is the tool's *structured content*. ``view_*`` tools also
    carry ``_meta.ui.resourceUri`` so a host knows to render the widget.
    """
    try:
        handler = _HANDLERS[name]
    except KeyError:
        raise KeyError(f"unknown tool: {name!r}")
    return handler(arguments or {})


def read_resource(uri: str) -> str:
    """The HTML for a ``ui://`` resource (served with :data:`RESOURCE_MIME_TYPE`)."""
    try:
        loader = _RESOURCE_HTML[uri]
    except KeyError:
        raise KeyError(f"unknown resource: {uri!r}")
    return loader()
