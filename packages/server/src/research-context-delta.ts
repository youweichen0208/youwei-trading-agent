const DELTA_LIMIT = 20;

const mapEvidence = (item: any) => ({
  id: item.id,
  source: item.source,
  eventKey: item.eventKey,
  published_at: item.published_at,
  observed_at: item.observed_at,
  payload: {
    title: String(item.payload?.title ?? "").slice(0, 180),
    url: item.payload?.url,
    excerpt: String(
      item.payload?.summary ?? item.payload?.text ?? item.payload?.content ?? "",
    ).slice(0, 900),
    excerptOnly: true,
  },
});

/**
 * Model-facing delta between two frozen contexts of the same symbol. Evidence
 * already returned by the previous context read is unchanged and omitted, so a
 * re-freeze after collection does not force the agent to page through the whole
 * snapshot again. The caller skips this when the symbols differ.
 */
export function contextDeltaView(
  snapshot: any,
  view: any,
  previousSnapshot: any,
) {
  const previousIds = new Set(
    (previousSnapshot.evidence ?? []).map((item: any) => item.id),
  );
  const current: any[] = snapshot.evidence ?? [];
  const fresh = current.filter((item: any) => !previousIds.has(item.id));
  const previousContextRef = previousSnapshot.contextRef ?? "上一次读取";
  const delta = {
    previousContextRef,
    newEvidence: fresh.length,
    unchangedEvidence: current.length - fresh.length,
    totalEvidence: current.length,
  };
  if (!fresh.length)
    return {
      ...view,
      evidence: [],
      evidencePage: {
        offset: 0,
        total: 0,
        nextOffset: null,
        instruction: `No new evidence since ${previousContextRef}. Previously returned evidence is unchanged and omitted; do not assume it is absent.`,
      },
      delta,
    };
  const page = fresh.slice(0, DELTA_LIMIT).map(mapEvidence);
  return {
    ...view,
    evidence: page,
    evidencePage: {
      offset: 0,
      total: fresh.length,
      nextOffset: null,
      instruction: `Only evidence new since ${previousContextRef} is included. Previously returned evidence is unchanged and omitted; do not assume it is absent.${
        fresh.length > DELTA_LIMIT
          ? ` First ${DELTA_LIMIT} new items are shown; re-read with a larger evidenceOffset or a narrower query if you need more.`
          : ""
      }`,
    },
    delta: {
      ...delta,
      ...(fresh.length > DELTA_LIMIT ? { truncated: true } : {}),
    },
  };
}
