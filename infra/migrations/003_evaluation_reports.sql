CREATE TABLE yta.evaluation_reports (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES yta.users(id),
  content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH','REPLAY','DEMO')),
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(owner_id, content_hash),
  CHECK (payload->>'data_mode' = data_mode)
);
CREATE INDEX evaluation_reports_owner ON yta.evaluation_reports(owner_id, created_at DESC);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.evaluation_reports
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
