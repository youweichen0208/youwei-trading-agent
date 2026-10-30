-- Fixed historical experiment evidence. Never grants model promotion authority.
CREATE TABLE yta.model_evaluation_records (
  id text PRIMARY KEY,
  calibration_id text NOT NULL REFERENCES yta.calibration_records(id),
  model_version_id text NOT NULL REFERENCES yta.model_versions(id),
  content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH','REPLAY','DEMO')),
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(calibration_id, content_hash),
  CHECK ((payload->>'schema_version' = 'experimental-price-evaluation-v1') IS TRUE),
  CHECK ((payload->>'calibration_id' = calibration_id) IS TRUE),
  CHECK ((payload->>'model_id' = model_version_id) IS TRUE),
  CHECK ((payload->>'content_hash' = content_hash) IS TRUE),
  CHECK ((payload->'cohort'->>'data_mode' = data_mode) IS TRUE),
  CHECK ((payload->>'status' = 'EXPERIMENTAL') IS TRUE),
  CHECK ((payload->>'qualification' = 'UNVERIFIED') IS TRUE)
);
CREATE INDEX model_evaluation_records_model ON yta.model_evaluation_records(model_version_id, created_at);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.model_evaluation_records
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
