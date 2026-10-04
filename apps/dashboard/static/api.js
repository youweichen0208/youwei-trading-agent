// fetch wrapper for the same-origin proxy: all calls go through
// /api/*, the tenant key never appears here (it lives in the proxy).

export async function getJSON(path, params = {}) {
  const url = new URL(`/api${path}`, window.location.origin);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  }
  const resp = await fetch(url, { headers: { Accept: "application/json" } });
  if (resp.status === 401) {
    // the browser already did Basic Auth; a 401 here means re-auth
    throw new Error("未认证（401）——请刷新页面重新登录");
  }
  if (!resp.ok) {
    let detail = "";
    try { detail = (await resp.json()).detail ?? ""; } catch { /* body not json */ }
    throw new Error(`请求失败 ${resp.status}${detail ? "：" + detail : ""}`);
  }
  return resp.json();
}

export async function getConfig() {
  return getJSON("/config");
}
