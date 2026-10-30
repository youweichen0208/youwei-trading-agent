CREATE TABLE yta.login_credentials (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES yta.users(id),
  password_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX credentials_latest ON yta.login_credentials(owner_id, id DESC);

CREATE TABLE yta.login_sessions (
  token_hash text PRIMARY KEY CHECK (token_hash ~ '^[a-f0-9]{64}$'),
  owner_id uuid NOT NULL REFERENCES yta.users(id),
  credential_id bigint NOT NULL REFERENCES yta.login_credentials(id),
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE yta.login_revocations (
  token_hash text PRIMARY KEY REFERENCES yta.login_sessions(token_hash),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE yta.research_sessions (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES yta.users(id),
  profile_id text NOT NULL CHECK (profile_id = 'alpha-orchestrator'),
  data_mode text NOT NULL CHECK (data_mode IN ('REPLAY','LIVE_RESEARCH','DEMO')),
  request_key text NOT NULL CHECK (length(request_key) BETWEEN 1 AND 128),
  request_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(owner_id, request_key),
  UNIQUE(id, owner_id)
);
CREATE INDEX research_sessions_owner ON yta.research_sessions(owner_id, created_at DESC, id);

CREATE TABLE yta.research_runs (
  id uuid PRIMARY KEY,
  session_id uuid NOT NULL,
  owner_id uuid NOT NULL REFERENCES yta.users(id),
  request_id uuid NOT NULL,
  request_hash text NOT NULL,
  prompt text NOT NULL CHECK (length(prompt) BETWEEN 1 AND 8000),
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(session_id, owner_id) REFERENCES yta.research_sessions(id, owner_id),
  UNIQUE(owner_id, request_id)
);
CREATE INDEX runs_session ON yta.research_runs(session_id, created_at, id);

CREATE TABLE yta.agent_events (
  event_id uuid PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES yta.research_runs(id),
  sequence integer NOT NULL CHECK (sequence > 0),
  type text NOT NULL,
  event jsonb NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(run_id, sequence),
  CHECK (event->>'event_id' = event_id::text),
  CHECK (event->>'run_id' = run_id::text),
  CHECK ((event->>'sequence')::integer = sequence),
  CHECK (event->>'type' = type)
);
CREATE UNIQUE INDEX one_terminal_event ON yta.agent_events(run_id)
  WHERE type IN ('run.completed','run.failed','run.canceled','run.execution_unknown');

CREATE TABLE yta.research_messages (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  session_id uuid NOT NULL REFERENCES yta.research_sessions(id),
  run_id uuid NOT NULL REFERENCES yta.research_runs(id),
  role text NOT NULL CHECK (role IN ('user','assistant')),
  content text NOT NULL,
  partial boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(run_id, role)
);
CREATE TABLE yta.run_cancellations (
  run_id uuid PRIMARY KEY REFERENCES yta.research_runs(id),
  owner_id uuid NOT NULL REFERENCES yta.users(id),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE yta.draft_requests (
  owner_id uuid NOT NULL REFERENCES yta.users(id),
  request_id uuid NOT NULL,
  draft_id uuid NOT NULL,
  FOREIGN KEY(draft_id, owner_id) REFERENCES yta.prediction_drafts(id, owner_id),
  PRIMARY KEY(owner_id, request_id)
);

DO $$
DECLARE fact_table text;
BEGIN
  FOREACH fact_table IN ARRAY ARRAY[
    'login_credentials','login_sessions','login_revocations','research_sessions',
    'research_runs','agent_events','research_messages','run_cancellations','draft_requests'
  ] LOOP
    EXECUTE format(
      'CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.%I '
      'FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation()', fact_table
    );
  END LOOP;
END;
$$;

