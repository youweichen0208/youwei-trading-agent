-- Historical screening evidence only; neither table grants approval authority.
CREATE TABLE yta.probability_quality_policies (
  id text PRIMARY KEY,
  policy_version text NOT NULL,
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH','REPLAY','DEMO')),
  horizon_sessions integer NOT NULL CHECK (horizon_sessions IN (1,3,5,10,20)),
  content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(policy_version,data_mode,horizon_sessions),
  CHECK ((id = 'probability-quality-policy-' || content_hash) IS TRUE),
  CHECK ((payload->>'schema_version' = 'probability-quality-policy-v1') IS TRUE),
  CHECK ((payload->>'policy_version' = policy_version) IS TRUE),
  CHECK ((payload->>'data_mode' = data_mode) IS TRUE),
  CHECK ((payload->>'horizon_sessions' = horizon_sessions::text) IS TRUE)
);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.probability_quality_policies
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();

CREATE TABLE yta.probability_quality_assessments (
  id text PRIMARY KEY,
  policy_id text NOT NULL REFERENCES yta.probability_quality_policies(id),
  calibration_id text NOT NULL REFERENCES yta.calibration_records(id),
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH','REPLAY','DEMO')),
  content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((id = 'probability-quality-assessment-' || content_hash) IS TRUE),
  CHECK ((payload->>'schema_version' = 'probability-quality-assessment-v1') IS TRUE),
  CHECK ((payload->>'qualification' = 'UNVERIFIED') IS TRUE),
  CHECK ((payload->'promotion_allowed' = 'false'::jsonb) IS TRUE),
  CHECK ((payload->>'content_hash' = content_hash) IS TRUE),
  CHECK ((policy_id = 'probability-quality-policy-' || (payload->>'policy_hash')) IS TRUE),
  CHECK ((payload->'policy'->>'data_mode' = data_mode) IS TRUE),
  CHECK ((payload->>'calibration_id' = calibration_id) IS TRUE)
);
CREATE INDEX probability_assessment_calibration ON yta.probability_quality_assessments(calibration_id,created_at);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.probability_quality_assessments
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
