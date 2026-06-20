"""Standalone-HTML export, shared by the ``export_*`` tools.

The widget renders inside a sandboxed iframe, which blocks file downloads — so
export lives *outside* the widget, as governed MCP tools. Each ``export_*`` tool
reuses the vendored single-file widget bundle with its parsed model injected as
``window.__MCP_MODEL__``, so the file renders the full interactive control in any
browser with no MCP host needed.
"""

from __future__ import annotations

import json
import os
import re
import tempfile


def _export_dir() -> str:
    """Where exported HTML is written.

    Prefers ``MCP_UI_COMPONENTS_EXPORT_DIR`` (set it in ``mcp.json`` to pin a
    folder), else the current working directory — which is the workspace folder
    when a host like VS Code launches the stdio server — falling back to the OS
    temp dir if the cwd isn't writable.
    """
    override = os.environ.get("MCP_UI_COMPONENTS_EXPORT_DIR")
    if override:
        return override
    cwd = os.getcwd()
    return cwd if os.access(cwd, os.W_OK) else tempfile.gettempdir()


def _slug(text: str) -> str:
    """A filesystem-safe slug from arbitrary title text."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text or "").strip("-")


def write_standalone_html(widget_html: str, model: dict, kind: str) -> dict:
    """Write a self-contained, *interactive* HTML copy of a widget to disk.

    The vendored single-file widget bundle is reused verbatim, with the parsed
    model injected as ``window.__MCP_MODEL__``. Opened in a browser (outside the
    host sandbox) the bundle renders the model directly — no MCP host needed —
    and its Export PNG/SVG buttons work there. Returns the file path; the host
    agent surfaces it (no megabyte payload echoed into the chat).

    ``kind`` is the widget slug (``"join-diagram"`` / ``"query-plan"``); it is
    always part of the filename so a join-diagram and a query-plan export of the
    *same* query don't collide and the file says which control produced it.

    Writes to the workspace (cwd) by default; see :func:`_export_dir`.
    """
    payload = json.dumps(model).replace("</", "<\\/")  # keep </script> out of the inline JSON
    inject = f"<script>window.__MCP_MODEL__ = {payload};</script>\n</head>"
    html = widget_html.replace("</head>", inject, 1)
    kind_slug = _slug(kind) or "diagram"
    base = _slug(model.get("title") or "")
    # e.g. "monthly-encounter-2025-join-diagram"; drop the base when it's empty or
    # is already just the kind (the parser's default title, e.g. "Join Diagram"),
    # avoiding "join-diagram-join-diagram".
    name = kind_slug if (not base or base.lower() == kind_slug) else f"{base}-{kind_slug}"
    path = os.path.join(_export_dir(), f"{name}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return {
        "structuredContent": {
            "path": path,
            "filename": f"{name}.html",
            "bytes": len(html),
            "note": "Self-contained interactive HTML written to disk (workspace "
                    "folder by default; set MCP_UI_COMPONENTS_EXPORT_DIR to change "
                    "it). Open it in a browser to view; use its Export PNG / Export "
                    "SVG buttons or Print there.",
        },
    }
