-- Experimental calibration evidence; no approval, champion, or forecast authority.
CREATE TABLE yta.calibration_records (
  id text PRIMARY KEY,
  model_version_id text NOT NULL REFERENCES yta.model_versions(id),
  content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH','REPLAY','DEMO')),
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(model_version_id, content_hash),
  CHECK (payload->>'model_id' = model_version_id),
  CHECK (payload->'report'->>'data_mode' = data_mode),
  CHECK (payload->'report'->>'status' = 'EXPERIMENTAL'),
  CHECK (payload->'report'->>'qualification' = 'UNVERIFIED')
);
CREATE INDEX calibration_records_model ON yta.calibration_records(model_version_id, created_at);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.calibration_records
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
