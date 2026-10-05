// Inject only in the isolated candidate browser, after the timing probe.
// First config disables direct connections; authenticated refresh enables them.
// A static model id verifies the fresh settings path without contacting a model.
(() => {
  const original = window.fetch.bind(window);
  let configs = 0;
  sessionStorage.removeItem("selectedModels");
  window.fetch = async (input, init) => {
    const path = new URL(
      typeof input === "string" || input instanceof URL ? input : input.url,
      location.href,
    ).pathname;
    const response = await original(input, init);
    if (!response.ok) return response;
    if (!["/api/config", "/api/v1/users/user/settings"].includes(path))
      return response;
    const body = await response.json();
    if (path === "/api/config") {
      body.features.enable_direct_connections = ++configs > 1;
    } else {
      body.ui = {
        ...body.ui,
        models: ["warm-direct-fixture"],
        directConnections: {
          OPENAI_API_BASE_URLS: ["https://fixture.invalid"],
          OPENAI_API_KEYS: ["fixture-key"],
          OPENAI_API_CONFIGS: {
            0: { enable: true, model_ids: ["warm-direct-fixture"] },
          },
        },
      };
    }
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
})();
