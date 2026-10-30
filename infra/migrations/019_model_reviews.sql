-- Review attempts preserve human intent and domain refusal. No approved state yet.
CREATE TABLE yta.model_reviews (
  id uuid PRIMARY KEY,
  proposal_id uuid NOT NULL REFERENCES yta.model_proposals(id),
  reviewer_id uuid NOT NULL REFERENCES yta.users(id),
  request_id uuid NOT NULL,
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH','REPLAY','DEMO')),
  assessment_id text NOT NULL REFERENCES yta.model_quality_assessments(id),
  previous_review_id uuid,
  revision integer NOT NULL CHECK (revision > 0),
  request_hash text NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
  content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(reviewer_id,data_mode,request_id),
  UNIQUE(proposal_id,revision),
  UNIQUE(proposal_id,id),
  FOREIGN KEY (proposal_id,previous_review_id) REFERENCES yta.model_reviews(proposal_id,id),
  CHECK ((revision=1) = (previous_review_id IS NULL)),
  CHECK (id IS DISTINCT FROM previous_review_id),
  CHECK ((payload->>'schema_version' = 'model-review-record-v1') IS TRUE),
  CHECK ((payload->>'proposal_id' = proposal_id::text) IS TRUE),
  CHECK ((payload->>'reviewer_id' = reviewer_id::text) IS TRUE),
  CHECK ((payload->>'request_id' = request_id::text) IS TRUE),
  CHECK ((payload->>'data_mode' = data_mode) IS TRUE),
  CHECK ((payload->>'assessment_id' = assessment_id) IS TRUE),
  CHECK ((payload->>'revision' = revision::text) IS TRUE),
  CHECK (payload ? 'previous_review_id'),
  CHECK ((payload->>'previous_review_id') IS NOT DISTINCT FROM previous_review_id::text),
  CHECK ((payload->>'content_hash' = content_hash) IS TRUE),
  CHECK ((payload->'promotion_allowed' = 'false'::jsonb) IS TRUE),
  CHECK (((payload->>'action' = 'APPROVE' AND payload->>'disposition' = 'BLOCKED')
    OR (payload->>'action' = 'REJECT' AND payload->>'disposition' = 'REJECTED')) IS TRUE)
);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.model_reviews
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
