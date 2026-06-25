import { defineConfig, devices } from "@playwright/test";

// Drives the vendored MIT reference host (demo/host.py) against the real MCP
// server. The host UI is on :8080, the MCP server on :8770, and the sandbox
// proxy (the widget's own origin) on :8081, exactly as the offline demo prints.
// The server is launched from the repo root so `python -m mcp_ui_components.server`
// resolves; the package must be installed first (pip install -e .).
export default defineConfig({
  testDir: ".",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:8080",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: "python demo/host.py",
    cwd: "..",
    url: "http://127.0.0.1:8080",
    timeout: 60_000,
    reuseExistingServer: !process.env.CI,
    stdout: "pipe",
    stderr: "pipe",
  },
});
