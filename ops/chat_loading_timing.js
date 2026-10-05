// Inject installChatReadyObserver before application scripts on each navigation.
// Collect with await readChatLoadingTiming() after navigation. No credentials,
// query strings, chat contents, or request/response bodies are recorded.
// preRequestWaitMs is a Resource Timing estimate (not exact CDP queue time).
export function installChatReadyObserver() {
  (() => {
    performance.setResourceTimingBufferSize(3000);
    const check = () => {
      const e = document.querySelector("#chat-input");
      if (
        e?.isContentEditable &&
        e.getClientRects().length &&
        getComputedStyle(e).visibility !== "hidden"
      ) {
        window.__chatReady = performance.now();
        window.__chatResources = performance.getEntriesByType("resource");
      } else requestAnimationFrame(check);
    };
    requestAnimationFrame(check);
  })();
}

export const readChatLoadingTiming = async () => {
  const deadline = Date.now() + 45000;
  while (!window.__chatReady && Date.now() < deadline)
    await new Promise((r) => setTimeout(r, 50));
  const n = performance.getEntriesByType("navigation")[0];
  const resources = (
    window.__chatResources ?? performance.getEntriesByType("resource")
  )
    .filter((r) => r.startTime < window.__chatReady)
    .map((r) => ({
      path: new URL(r.name).pathname.replace(/\/[0-9a-f-]{32,}/g, "/[id]"),
      protocol: r.nextHopProtocol,
      status: r.responseStatus,
      durationMs: r.duration,
      transferBytes: r.transferSize,
      encodedBytes: r.encodedBodySize,
      preRequestWaitMs: Math.max(
        0,
        r.requestStart -
          r.fetchStart -
          (r.domainLookupEnd - r.domainLookupStart) -
          (r.connectEnd - r.connectStart),
      ),
      ttfbMs: r.responseStart - r.requestStart,
    }));
  return {
    readyMs: window.__chatReady ?? null,
    protocol: n.nextHopProtocol,
    htmlMs: n.responseEnd,
    resources,
    resourceTransferBytes: resources.reduce((a, r) => a + r.transferBytes, 0),
    resourceEncodedBytes: resources.reduce((a, r) => a + r.encodedBytes, 0),
    maxPreRequestWaitMs: Math.max(...resources.map((r) => r.preRequestWaitMs)),
    apis: resources.filter(
      (r) => r.path.startsWith("/api/") || r.path.startsWith("/openai/"),
    ),
  };
};
