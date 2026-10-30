-- A combined screening fact binds evidence; it never authorizes promotion.
CREATE TABLE yta.model_quality_gates (
  id text PRIMARY KEY,
  proposal_id uuid NOT NULL REFERENCES yta.model_proposals(id),
  price_assessment_id text NOT NULL REFERENCES yta.model_quality_assessments(id),
  probability_assessment_id text NOT NULL REFERENCES yta.probability_quality_assessments(id),
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH','REPLAY','DEMO')),
  content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((id = 'model-quality-gate-' || content_hash) IS TRUE),
  CHECK ((payload->>'schema_version' = 'model-quality-gate-v1') IS TRUE),
  CHECK ((payload->>'proposal_id' = proposal_id::text) IS TRUE),
  CHECK ((payload->>'price_assessment_id' = price_assessment_id) IS TRUE),
  CHECK ((payload->>'probability_assessment_id' = probability_assessment_id) IS TRUE),
  CHECK ((payload->>'data_mode' = data_mode) IS TRUE),
  CHECK ((payload->>'content_hash' = content_hash) IS TRUE),
  CHECK ((payload->>'qualification' = 'UNVERIFIED') IS TRUE),
  CHECK ((payload->'promotion_allowed' = 'false'::jsonb) IS TRUE)
);
CREATE INDEX quality_gates_proposal ON yta.model_quality_gates(proposal_id,created_at);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.model_quality_gates
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
