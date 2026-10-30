import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

const spec = JSON.parse(readFileSync(new URL("../bff.openapi.json", import.meta.url), "utf8"));
const controller = readFileSync(new URL("../../../apps/bff/src/app.ts", import.meta.url), "utf8");

it("covers every implemented controller route and its actual success status", () => {
  const methods = [...controller.matchAll(/@(Get|Post)\("([^"]+)"\)([\s\S]*?)(?=@(?:Get|Post)\(|\n}\n)/g)];
  expect(methods.length).toBe([...controller.matchAll(/@(Get|Post|Put|Patch|Delete|Options|Head)\(/g)].length);
  const actual = methods.map(match => ({method: match[1]!.toLowerCase(), path: "/" + match[2]!.replace(/:([a-z]+)/g, "{$1}"),
    status: Number(match[3]!.match(/@HttpCode\((\d+)\)/)?.[1] ?? (match[1] === "Post" ? 201 : 200))}));
  const documented = Object.entries(spec.paths).flatMap(([path, operations]) => Object.keys(operations as object).map(method => `${method} ${path}`));
  expect(documented.sort()).toEqual(actual.map(({method, path}) => `${method} ${path}`).sort());
  for (const {method, path, status} of actual) expect(spec.paths[path][method].responses[String(status)]).toBeDefined();
  const ids = Object.values(spec.paths).flatMap(operations => Object.values(operations as object).map(operation => operation.operationId));
  expect(new Set(ids).size).toBe(actual.length);
});

it("resolves all schema references locally without exposing internal routes or bearer credentials", () => {
  function walk(value: unknown) {
    if (!value || typeof value !== "object") return;
    if ("$ref" in value) {
      expect(typeof value.$ref).toBe("string");
      const path = String(value.$ref);
      expect(path.startsWith("#/components/schemas/")).toBe(true);
      expect(spec.components.schemas[path.slice("#/components/schemas/".length)]).toBeDefined();
    }
    for (const child of Object.values(value)) walk(child);
  }
  walk(spec);
  expect(spec.servers).toEqual([{url: "/api/yta/v1"}]);
  expect(Object.keys(spec.paths).some(path => path.includes("internal"))).toBe(false);
  expect(JSON.stringify(spec.components.securitySchemes)).not.toContain("bearer");
});

it("documents session and CSRF together for mutations and explicitly models SSE/readiness", () => {
  for (const [path, operations] of Object.entries(spec.paths)) {
    for (const [method, operation] of Object.entries(operations as Record<string, any>)) {
      if (["/health", "/ready", "/auth/login"].includes(path)) expect(operation.security).toEqual([]);
      else expect(operation.security).toEqual(method === "post"
        ? [{session: [], csrf: []}, {secureSession: [], csrf: []}]
        : [{session: []}, {secureSession: []}]);
      if (method === "post") expect(operation.parameters).toContainEqual(expect.objectContaining({name: "Origin", required: true}));
    }
  }
  expect(spec.paths["/ready"].get.responses[503].content["application/json"].schema.$ref).toBe("#/components/schemas/Readiness");
  const stream = spec.paths["/runs/{id}/events"].get;
  expect(stream.responses[200].content["text/event-stream"]["x-event-schema"].$ref).toBe("#/components/schemas/agentEventSchema");
  expect(stream.parameters).toContainEqual(expect.objectContaining({name: "Last-Event-ID", in: "header"}));
  expect(spec.components.schemas.freezeRequestSchema.properties.confirmed).toEqual({type: "boolean", const: true});
  expect(spec.components.schemas.freezeRequestSchema.additionalProperties).toBe(false);
});
