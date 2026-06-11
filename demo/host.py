"""Local offline demo — render the lineage widget in a real MCP Apps host.

This launches three things and wires them together so you can see the widget as
an interactive control, exactly as it appears in VS Code Copilot Chat:

  1. the lineage MCP server (streamable HTTP, port 8770)   ← our code
  2. the MCP Apps reference host UI       (port 8080)       ← vendored, MIT
  3. the sandbox proxy on a second origin (port 8081)       ← vendored, MIT

The reference host (modelcontextprotocol/ext-apps `basic-host`) speaks the same
protocol VS Code uses, so this demo is a faithful preview. The host connects to
our server, calls `view_lineage`, renders the returned `ui://lineage/viewer.html`
widget inside the sandbox, and proxies the widget's "Expand upstream" click back
as an `expand_lineage_node` tool call.

    python demo/host.py        # then open http://localhost:8080

Requires the MCP SDK (`pip install mcp`) for the server; the host UI is static.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
VENDOR = Path(__file__).resolve().parent / "_vendor_host"

MCP_PORT = 8770
HOST_PORT = 8080
SANDBOX_PORT = 8081  # hard-coded in the vendored host bundle; do not change
MCP_URL = f"http://localhost:{MCP_PORT}/mcp"

# CSP applied to the sandbox origin. Mirrors the reference host's serve.ts with
# no extra domains — our widget is fully self-contained (no external network).
SANDBOX_CSP = "; ".join([
    "default-src 'self' 'unsafe-inline'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob: data:",
    "style-src 'self' 'unsafe-inline' blob: data:",
    "img-src 'self' data: blob:",
    "font-src 'self' data: blob:",
    "connect-src 'self'",
    "worker-src 'self' blob:",
    "frame-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
])


class HostHandler(BaseHTTPRequestHandler):
    """Serves the host UI (8080): index.html + /api/servers."""

    def log_message(self, *_):  # quiet
        pass

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/servers":
            body = json.dumps([MCP_URL]).encode()
            self._send(200, body, "application/json")
        elif path in ("/", "/index.html"):
            self._send_file(VENDOR / "index.html", "text/html; charset=utf-8")
        elif path == "/sandbox.html":
            self._send(404, b"Sandbox is served on port 8081", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def _send_file(self, p: Path, ctype, extra=None):
        data = p.read_bytes()
        self._send(200, data, ctype, extra)

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


class SandboxHandler(BaseHTTPRequestHandler):
    """Serves the sandbox proxy (8081) with a tamper-proof CSP header."""

    def log_message(self, *_):
        pass

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/sandbox.html"):
            data = (VENDOR / "sandbox.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", SANDBOX_CSP)
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Only sandbox.html is served here")


def _serve(handler, port):
    ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


def main() -> None:
    if not (VENDOR / "index.html").exists():
        raise SystemExit("Missing vendored host bundle in demo/_vendor_host/.")

    # 1) Launch our MCP server (HTTP) as a child process.
    env_path = str(ROOT / "src")
    proc = subprocess.Popen(
        [sys.executable, "-m", "lineage_mcp.server", "--http", "--port", str(MCP_PORT)],
        cwd=str(ROOT),
        env={**_env(), "PYTHONPATH": env_path},
    )

    # 2) Serve the host UI + sandbox proxy on their two origins.
    threading.Thread(target=partial(_serve, HostHandler, HOST_PORT), daemon=True).start()
    threading.Thread(target=partial(_serve, SandboxHandler, SANDBOX_PORT), daemon=True).start()

    print("\n  Lineage MCP Apps — offline demo")
    print("  ==============================")
    print(f"  MCP server : {MCP_URL}")
    print(f"  Host UI    : http://localhost:{HOST_PORT}")
    print(f"  Sandbox    : http://localhost:{SANDBOX_PORT} (separate origin)")
    print()
    print(f"  Open  http://localhost:{HOST_PORT}/?tool=view_lineage&call=true")
    print("  to auto-call the tool, or pick view_lineage and click 'Call Tool'.")
    print("  Then click 'Expand upstream' inside the widget — it is proxied")
    print("  back through the host as an expand_lineage_node tool call.\n")
    print("  Ctrl+C to stop.\n")

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n  stopping…")
    finally:
        proc.terminate()


def _env():
    import os
    return dict(os.environ)


if __name__ == "__main__":
    main()
