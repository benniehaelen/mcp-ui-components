"""The UI components.

Each subpackage is one self-contained MCP UI control — a UI-enabled ``view_*``
tool, the proxied tools its widget calls, the ``ui://`` resource it renders, and
its provider. Every component exposes the same four module-level names
(``TOOLS``, ``RESOURCES``, ``HANDLERS``, ``RESOURCE_HTML``) that
:mod:`mcp_ui_components.registry` merges into the server's tool/resource surface.
"""
