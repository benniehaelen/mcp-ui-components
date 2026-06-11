# Vendored MCP Apps reference host

`index.html` and `sandbox.html` are the **prebuilt** single-file bundles of the
official MCP Apps reference host:

> https://github.com/modelcontextprotocol/ext-apps — `examples/basic-host`
> (MIT License — see `LICENSE`)

They are vendored here only so the local demo (`python demo/host.py`) can render
the lineage widget through the **real MCP Apps protocol** without requiring Node
at runtime. This is the same host/protocol VS Code Copilot uses, so what you see
in the demo is what you get in VS Code.

- `index.html` — the host UI (served on port 8080)
- `sandbox.html` — the double-iframe sandbox proxy (served on port 8081, a
  separate origin, with CSP applied via HTTP headers)

To rebuild them yourself: clone ext-apps, `cd examples/basic-host`,
`npm install`, then `INPUT=index.html npx vite build` and
`INPUT=sandbox.html npx vite build`.
