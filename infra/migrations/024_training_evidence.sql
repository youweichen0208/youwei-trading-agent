-- Full original training inputs and retraining comparisons; never promotion authority.
CREATE TABLE yta.training_evidence (
  id text PRIMARY KEY,
  model_id text NOT NULL REFERENCES yta.model_versions(id),
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH','REPLAY','DEMO')),
  content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((id = 'training-evidence-' || content_hash) IS TRUE),
  CHECK ((payload->>'schema_version' = 'training-evidence-v1') IS TRUE),
  CHECK ((payload->>'model_id' = model_id) IS TRUE),
  CHECK ((payload->>'data_mode' = data_mode) IS TRUE),
  CHECK ((payload->'report'->>'schema_version' = 'training-replay-v1') IS TRUE),
  CHECK ((payload->'report'->>'expected_data_mode' = data_mode) IS TRUE),
  CHECK ((payload->'report'->>'replayed_data_mode' = data_mode) IS TRUE),
  CHECK ((payload->'report'->>'status' IN ('MATCHED','MISMATCH')) IS TRUE),
  CHECK ((payload->'report'->>'qualification' = 'UNVERIFIED') IS TRUE),
  CHECK ((payload->'report'->'promotion_allowed' = 'false'::jsonb) IS TRUE)
);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.training_evidence
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
