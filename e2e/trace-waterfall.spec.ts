import { test, expect, type Frame, type Page } from "@playwright/test";

// The three knowledge-graph lookups that arrive lazily under "Resolve Joins with KG".
const KG_LOOKUPS = [
  "kg.resolve.encounters",
  "kg.resolve.patients",
  "kg.resolve.stg_charges",
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
            const count = await frame.locator(".name-text", { hasText: "Resolve Joins with KG" }).count();
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

test("view_trace_waterfall renders and expand_span_children reveals the knowledge-graph lookups", async ({ page }) => {
  await page.goto("/?tool=view_trace_waterfall&call=true");

  const frame = await waterfallFrame(page);

  // The "Resolve Joins with KG" step is present; its lazy KG lookups are not yet shown.
  await expect(frame.locator(".name-text", { hasText: "Resolve Joins with KG" })).toBeVisible();
  for (const lookup of KG_LOOKUPS) {
    await expect(frame.locator(".name-text", { hasText: lookup })).toHaveCount(0);
  }

  // Click the + on "Resolve Joins with KG" -> proxied expand_span_children.
  const stepRow = frame.locator(".row", {
    has: frame.locator(".name-text", { hasText: "Resolve Joins with KG" }),
  });
  await stepRow.locator(".twirl").click();

  // The three knowledge-graph lookups now appear.
  for (const lookup of KG_LOOKUPS) {
    await expect(frame.locator(".name-text", { hasText: lookup })).toBeVisible();
  }
});
