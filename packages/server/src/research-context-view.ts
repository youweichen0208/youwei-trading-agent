/** Model transport view; the stored snapshot and original evidence remain unchanged. */
export function researchContextView(snapshot: any, offset = 0) {
  const evidence = snapshot.evidence ?? [];
  const page = evidence.slice(offset, offset + 5).map((item: any) => ({
    id: item.id,
    source: item.source,
    eventKey: item.eventKey,
    published_at: item.published_at,
    observed_at: item.observed_at,
    payload: {
      title: String(item.payload?.title ?? "").slice(0, 180),
      url: item.payload?.url,
      excerpt: String(
        item.payload?.summary ??
          item.payload?.text ??
          item.payload?.content ??
          "",
      ).slice(0, 900),
      excerptOnly: true,
    },
  }));
  return {
    ...snapshot,
    evidence: page,
    evidencePage: {
      offset,
      total: evidence.length,
      nextOffset: offset + 5 < evidence.length ? offset + 5 : null,
      instruction:
        "Read further pages using this same contextRef and evidenceOffset. Evidence text is excerpted; do not assume omitted details are absent.",
    },
  };
}
