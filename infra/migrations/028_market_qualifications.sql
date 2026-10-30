-- Qualification is a new fact; raw acquisition records remain UNVERIFIED.
CREATE TABLE yta.market_qualifications (
  hash text PRIMARY KEY CHECK (hash ~ '^[a-f0-9]{64}$'),
  acquisition_hash text NOT NULL REFERENCES yta.provider_acquisitions(hash),
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH','REPLAY','DEMO')),
  as_of timestamptz NOT NULL,
  evidence jsonb NOT NULL,
  snapshot jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((snapshot->>'data_mode'=data_mode) IS TRUE),
  CHECK ((snapshot->>'provider'='massive') IS TRUE),
  CHECK ((snapshot->>'symbol'='NVDA') IS TRUE)
);
CREATE INDEX market_qualifications_latest ON yta.market_qualifications(data_mode,as_of DESC,hash);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.market_qualifications
  FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
