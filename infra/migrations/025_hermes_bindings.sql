-- Private protocol facts. A committed request is a send claim, never permission to resend.
ALTER TABLE yta.research_runs ADD CONSTRAINT research_run_session_owner
  UNIQUE(id, session_id, owner_id);
CREATE TABLE yta.hermes_session_bindings (
  session_id uuid PRIMARY KEY,
  owner_id uuid NOT NULL,
  runtime_hash text NOT NULL CHECK(runtime_hash ~ '^[a-f0-9]{64}$'),
  session_key text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(session_id,owner_id) REFERENCES yta.research_sessions(id,owner_id),
  UNIQUE(session_id,owner_id,runtime_hash),
  CHECK(session_key='yta-session-' || session_id::text)
);
CREATE TABLE yta.hermes_run_requests (
  run_id uuid PRIMARY KEY,
  session_id uuid NOT NULL,
  owner_id uuid NOT NULL,
  runtime_hash text NOT NULL,
  request_key text NOT NULL UNIQUE,
  request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
  body jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(run_id,session_id,owner_id) REFERENCES yta.research_runs(id,session_id,owner_id),
  FOREIGN KEY(session_id,owner_id,runtime_hash) REFERENCES yta.hermes_session_bindings(session_id,owner_id,runtime_hash),
  CHECK(request_key='yta-run-' || run_id::text),
  CHECK((jsonb_typeof(body)='object' AND jsonb_typeof(body->'input')='string'
    AND jsonb_typeof(body->'model')='string') IS TRUE)
);
CREATE TABLE yta.hermes_run_receipts (
  run_id uuid PRIMARY KEY REFERENCES yta.hermes_run_requests(run_id),
  upstream_run_id text NOT NULL UNIQUE CHECK(upstream_run_id ~ '^run_[a-f0-9]{32}$'),
  created_at timestamptz NOT NULL DEFAULT now()
);
DO $$
DECLARE fact_table text;
BEGIN
  FOREACH fact_table IN ARRAY ARRAY['hermes_session_bindings','hermes_run_requests','hermes_run_receipts'] LOOP
    EXECUTE format('CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.%I '
      'FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation()', fact_table);
  END LOOP;
END;
$$;
