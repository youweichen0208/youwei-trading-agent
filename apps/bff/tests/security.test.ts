import { describe, expect, it } from "vitest";
import { readConfig } from "../src/config.js";
import { hashPassword } from "../src/auth.js";

const env = { YTA_DATABASE_URL: "postgresql://local/test", YTA_ALPHA_TOKEN: "a-test-only-service-token-of-more-than-32-characters" };
const hermes = {YTA_RESEARCH_DRIVER: "HERMES", YTA_HERMES_ORIGIN: "http://hermes:8642",
  YTA_HERMES_TOKEN: "gateway-test-credential-distinct-123456789", YTA_HERMES_MODEL: "test-model"};
it("requires explicit complete Hermes configuration and keeps deployment gates closed", () => {
  expect(readConfig(env).YTA_RESEARCH_DRIVER).toBe("AUTO");
  expect(readConfig({...env,...hermes}).YTA_RESEARCH_DRIVER).toBe("HERMES");
  for (const key of ["YTA_HERMES_ORIGIN", "YTA_HERMES_TOKEN", "YTA_HERMES_MODEL"]) {
    expect(() => readConfig({...env,...hermes,[key]:undefined})).toThrow(/Hermes/);
  }
  expect(() => readConfig({...env,...hermes,YTA_RESEARCH_DRIVER:"AUTO"})).toThrow(/explicit/);
  expect(() => readConfig({...env,...hermes,YTA_HERMES_TOKEN:env.YTA_ALPHA_TOKEN})).toThrow(/distinct/);
  for (const YTA_ENV of ["staging", "production"]) {
    expect(() => readConfig({...env,...hermes,YTA_ENV,YTA_DATA_MODE:"LIVE_RESEARCH",YTA_WEB_ORIGIN:"https://yta.test"})).toThrow(/unverified/);
  }
});
it("rejects unsafe Hermes connection values without printing credentials", () => {
  for (const YTA_HERMES_ORIGIN of ["http://user:private-marker@hermes:8642", "http://hermes:8642/path", "http://hermes:8642?private-marker", "file:///private-marker"]) {
    expect(() => readConfig({...env,...hermes,YTA_HERMES_ORIGIN})).toThrow(/origins/);
    try {readConfig({...env,...hermes,YTA_HERMES_ORIGIN});} catch (error) {expect(String(error)).not.toContain("private-marker");}
  }
  expect(() => readConfig({...env,...hermes,YTA_HERMES_TOKEN:hermes.YTA_HERMES_TOKEN+"\r\nsecret"})).toThrow(/configuration/);
});
describe("configuration and password safety", () => {
  it("rejects production replay, non-HTTPS public origins and credential URLs", () => {
    expect(() => readConfig({ ...env, YTA_ENV: "production" })).toThrow();
    expect(() => readConfig({ ...env, YTA_ALPHA_URL: "http://secret:token@localhost:8100" })).toThrow();
    expect(() => readConfig({ ...env, YTA_ALPHA_TOKEN: "example" })).toThrow(/configuration/);
  });
  it("binds locally unless container networking is explicitly configured", () => {
    expect(readConfig(env).YTA_BFF_HOST).toBe("127.0.0.1");
    expect(readConfig({...env, YTA_BFF_HOST: "0.0.0.0"}).YTA_BFF_HOST).toBe("0.0.0.0");
    expect(() => readConfig({...env, YTA_BFF_HOST: "public-host"})).toThrow(/configuration/);
  });
  it("uses salted one-way password hashes", async () => {
    const a = await hashPassword("synthetic-password-not-live");
    const b = await hashPassword("synthetic-password-not-live");
    expect(a).not.toEqual(b);
    expect(a).toMatch(/^scrypt-v1:[a-f0-9]{32}:[a-f0-9]{128}$/);
    expect(a).not.toContain("synthetic-password");
  });
});

it("rejects tracing endpoints containing credentials, query data or a wrong path", () => {
  for (const endpoint of ["http://user:secret@localhost/v1/traces", "file:///v1/traces",
    "http://localhost/v1/traces?token=secret", "http://localhost/v1/traces#secret", "http://localhost/wrong"]) {
    expect(() => readConfig({...env, YTA_OTLP_TRACES_URL: endpoint})).toThrow();
  }
  expect(readConfig(env).YTA_OTLP_TRACES_URL).toBeUndefined();
});

it("write admission accepts only explicit server policy values", () => {
  expect(readConfig(env).YTA_WRITE_ADMISSION).toBe("OPEN");
  expect(readConfig({...env, YTA_WRITE_ADMISSION: "PAUSED"}).YTA_WRITE_ADMISSION).toBe("PAUSED");
  for (const value of ["false", "true", "paused", "", "0"]) {
    expect(() => readConfig({...env, YTA_WRITE_ADMISSION: value})).toThrow(/configuration/);
  }
});

it("model reviewer configuration defaults to nobody and accepts only distinct explicit UUIDs",()=>{
  expect(readConfig(env).YTA_MODEL_REVIEWER_IDS).toEqual([]);
  const id="abcdefab-1234-4567-8901-abcdefabcdef";
  expect(readConfig({...env,YTA_MODEL_REVIEWER_IDS:" "+id.toUpperCase()+" "}).YTA_MODEL_REVIEWER_IDS).toEqual([id]);
  for(const value of ["*","admin"," ",id+",",id+","+id.toUpperCase(),JSON.stringify([id])]) {
    expect(()=>readConfig({...env,YTA_MODEL_REVIEWER_IDS:value})).toThrow(/configuration/);
  }
});
