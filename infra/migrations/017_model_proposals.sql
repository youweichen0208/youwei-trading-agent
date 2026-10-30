-- A submission is evidence for review, never an approval or champion assignment.
CREATE TABLE yta.model_proposals (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES yta.users(id),
  request_id uuid NOT NULL,
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH','REPLAY','DEMO')),
  candidate_evaluation_id text NOT NULL REFERENCES yta.model_evaluation_records(id),
  reference_evaluation_id text REFERENCES yta.model_evaluation_records(id),
  request_hash text NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
  content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(owner_id, data_mode, request_id),
  CHECK (reference_evaluation_id IS NULL OR reference_evaluation_id <> candidate_evaluation_id),
  CHECK ((payload->>'schema_version' = 'model-proposal-v1') IS TRUE),
  CHECK ((payload->>'status' = 'SUBMITTED') IS TRUE),
  CHECK ((payload->>'qualification' = 'UNVERIFIED') IS TRUE),
  CHECK ((payload->>'owner_id' = owner_id::text) IS TRUE),
  CHECK ((payload->>'request_id' = request_id::text) IS TRUE),
  CHECK ((payload->>'data_mode' = data_mode) IS TRUE),
  CHECK ((payload->>'candidate_evaluation_id' = candidate_evaluation_id) IS TRUE),
  CHECK (payload ? 'reference_evaluation_id'),
  CHECK ((payload->>'reference_evaluation_id') IS NOT DISTINCT FROM reference_evaluation_id)
);
CREATE INDEX model_proposals_owner ON yta.model_proposals(owner_id, data_mode, created_at);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.model_proposals
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
