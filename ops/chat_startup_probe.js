// Candidate-only browser probe. Inject before application scripts; it records
// paths and timings only. Synthetic delays make ordering assertions repeatable.
export function installChatStartupProbe() {
  const original = window.fetch.bind(window);
  const calls = (window.__startupCalls = []);
  let configCount = 0;
  window.fetch = async (input, init) => {
    const path = new URL(
      typeof input === "string" || input instanceof URL ? input : input.url,
      location.href,
    ).pathname;
    const selected = [
      "/api/config",
      "/api/v1/auths/",
      "/api/v1/users/user/settings",
      "/api/models",
    ].includes(path);
    if (!selected) return original(input, init);
    const call = { path, startMs: performance.now(), endMs: null };
    calls.push(call);
    const delay =
      path === "/api/config" && ++configCount === 1
        ? 500
        : path === "/api/v1/users/user/settings"
          ? 1000
          : 0;
    if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
    try {
      return await original(input, init);
    } finally {
      call.endMs = performance.now();
    }
  };
}

export function readChatStartupProbe() {
  const calls = window.__startupCalls;
  const configs = calls.filter((call) => call.path === "/api/config");
  const session = calls.find((call) => call.path === "/api/v1/auths/");
  const settings = calls.find((call) => call.path === "/api/v1/users/user/settings");
  const models = calls.find((call) => call.path === "/api/models");
  const checks = {
    sessionParallelWithConfig: session?.startMs < configs[0]?.endMs,
    authenticatedConfigRefreshRetained:
      configs.length === 2 && configs[1].startMs >= session?.endMs,
    modelsParallelWithSettings: models?.startMs < settings?.endMs,
    singleSessionSettingsModels:
      ["/api/v1/auths/", "/api/v1/users/user/settings", "/api/models"].every(
        (path) => calls.filter((call) => call.path === path).length === 1,
      ),
    inputEditable: !!document.querySelector("#chat-input")?.isContentEditable,
  };
  return { checks, passed: Object.values(checks).every(Boolean), calls };
}
