-- Upstream cursor and its safe public projection commit together; no reasoning/preview storage.
CREATE TABLE yta.hermes_run_events (
  run_id uuid NOT NULL REFERENCES yta.hermes_run_receipts(run_id),
  sequence integer NOT NULL CHECK(sequence >= 0),
  observation jsonb NOT NULL,
  content_hash text NOT NULL CHECK(content_hash ~ '^[a-f0-9]{64}$'),
  public_event_id uuid UNIQUE REFERENCES yta.agent_events(event_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(run_id,sequence),
  CHECK((jsonb_typeof(observation)='object'
    AND (observation->>'seq')::integer=sequence
    AND jsonb_typeof(observation->'event')='string') IS TRUE)
);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.hermes_run_events
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
