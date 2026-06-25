import { test, expect, type Frame, type Page } from "@playwright/test";

// The six resolver steps that arrive lazily under resolve.pipeline.
const RESOLVER_STEPS = [
  "resolve.DateResolver",
  "resolve.RecencyResolver",
  "resolve.GrainResolver",
  "resolve.ZoneRouter",
  "resolve.BusinessRuleResolver",
  "resolve.AuthorityReranker",
];

// The widget renders inside a sandboxed iframe (its own origin via the sandbox
// proxy). Find the frame that actually holds the waterfall, retrying while the
// host connects and the auto-called view_trace_waterfall renders.
async function waterfallFrame(page: Page): Promise<Frame> {
  let found: Frame | null = null;
  await expect
    .poll(
      async () => {
        for (const frame of page.frames()) {
          try {
            const count = await frame.locator(".name-text", { hasText: "resolve.pipeline" }).count();
            if (count > 0) { found = frame; return true; }
          } catch {
            // frame detached mid-poll; ignore and retry
          }
        }
        return false;
      },
      { timeout: 40_000, message: "trace waterfall did not render in any frame" },
    )
    .toBe(true);
  return found!;
}

test("view_trace_waterfall renders and expand_span_children reveals the six resolver spans", async ({ page }) => {
  await page.goto("/?tool=view_trace_waterfall&call=true");

  const frame = await waterfallFrame(page);

  // The pipeline row is present; its six lazy children are not shown yet.
  await expect(frame.locator(".name-text", { hasText: "resolve.pipeline" })).toBeVisible();
  for (const step of RESOLVER_STEPS) {
    await expect(frame.locator(".name-text", { hasText: step })).toHaveCount(0);
  }

  // Click the + on resolve.pipeline -> proxied expand_span_children.
  const pipelineRow = frame.locator(".row", {
    has: frame.locator(".name-text", { hasText: "resolve.pipeline" }),
  });
  await pipelineRow.locator(".twirl").click();

  // All six resolver spans now appear.
  for (const step of RESOLVER_STEPS) {
    await expect(frame.locator(".name-text", { hasText: step })).toBeVisible();
  }
});
