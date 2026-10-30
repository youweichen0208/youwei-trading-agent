import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { Router, type RequestHandler } from "express";
import type { Auth } from "./auth.js";

const require = createRequire(import.meta.url);
const prefix = "/api/yta/v1/docs";
const html = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>YTA API 文档</title><link rel="stylesheet" href="${prefix}/swagger-ui.css"></head>
<body><div id="swagger-ui"></div><script src="${prefix}/swagger-ui-bundle.js"></script><script src="${prefix}/init.js"></script></body></html>`;
const initializer = `SwaggerUIBundle({url: "${prefix}/openapi.json", dom_id: "#swagger-ui",
  presets: [SwaggerUIBundle.presets.apis], layout: "BaseLayout", deepLinking: true,
  validatorUrl: null, queryConfigEnabled: false, persistAuthorization: false,
  supportedSubmitMethods: [], defaultModelsExpandDepth: -1, displayOperationId: true});`;

export function documentationRouter(auth: Auth): Router {
  const router = Router();
  const authenticated: RequestHandler = (request, _response, next) => {
    void auth.require(request).then(() => next(), next);
  };
  const spec = readFileSync(require.resolve("@yta/contracts/bff.openapi.json"));
  const assets = new Map<string, {type: string; content: string | Buffer}>([
    ["swagger-ui.css", {type: "text/css", content: readFileSync(require.resolve("swagger-ui-dist/swagger-ui.css"))}],
    ["swagger-ui-bundle.js", {type: "application/javascript", content: readFileSync(require.resolve("swagger-ui-dist/swagger-ui-bundle.js"))}],
    ["init.js", {type: "application/javascript", content: initializer}],
    ["openapi.json", {type: "application/json", content: spec}],
  ]);
  router.get(prefix, authenticated, (_request, response) => {
    response.setHeader("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'");
    response.type("html").send(html);
  });
  for (const [name, asset] of assets) router.get(`${prefix}/${name}`, authenticated, (_request, response) => {
    response.type(asset.type).send(asset.content);
  });
  return router;
}
