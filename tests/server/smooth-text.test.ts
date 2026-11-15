import { test } from "node:test";
import assert from "node:assert/strict";
import { revealRate, revealStep } from "../../apps/web/lib/smooth-text.js";

const FRAME = 16;

/** Frames-to-clear simulation matching the hook: a growing backlog raises the rate once. */
function timeToClear(backlog: number) {
  let shown = 0,
    elapsed = 0,
    previous = 0,
    rate = revealRate(0);
  while (shown < backlog && elapsed < 5000) {
    const remaining = backlog - shown;
    if (remaining > previous) rate = revealRate(remaining);
    previous = remaining;
    shown += revealStep(remaining, FRAME, rate);
    elapsed += FRAME;
  }
  return { elapsed, shown };
}

test("small buffers reveal at a readable pace", () => {
  assert.equal(revealStep(0, FRAME, revealRate(0)), 0);
  assert.equal(revealStep(4, FRAME, revealRate(4)), 1);
});

test("a fresh chunk clears within the catch-up window", () => {
  for (const backlog of [40, 120, 400, 1200]) {
    const { elapsed, shown } = timeToClear(backlog);
    assert.equal(shown, backlog);
    assert.ok(elapsed <= 500, `backlog ${backlog} took ${elapsed}ms`);
  }
});

test("never reveals more than the buffered text", () => {
  assert.equal(revealStep(2, 1000, revealRate(1000)), 2);
});
