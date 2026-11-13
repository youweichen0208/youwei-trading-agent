/** Model transport view; the stored snapshot and original evidence remain unchanged. */
export function researchContextView(snapshot: any, offset = 0) {
  const evidence = snapshot.evidence ?? [];
  const candidates = evidence.slice(offset, offset + 20).map((item: any) => ({
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
  const page: any[] = [];
  const response = {
    ...snapshot,
    evidence: page,
    evidencePage: {
      offset,
      total: evidence.length,
      nextOffset: null as number | null,
      instruction:
        "Read further pages using this same contextRef and evidenceOffset. Evidence text is excerpted; do not assume omitted details are absent.",
    },
  };
  for (const item of candidates) {
    page.push(item);
    response.evidencePage.nextOffset =
      offset + page.length < evidence.length ? offset + page.length : null;
    // Preserve the existing transport envelope while packing short evidence.
    if (page.length > 1 && JSON.stringify(response).length >= 9500) {
      page.pop();
      break;
    }
  }
  response.evidencePage.nextOffset =
    offset + page.length < evidence.length ? offset + page.length : null;
  return response;
}
