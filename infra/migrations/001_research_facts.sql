CREATE SCHEMA IF NOT EXISTS yta;

CREATE TABLE yta.users (
  id uuid PRIMARY KEY,
  email text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE yta.model_versions (
  id text PRIMARY KEY,
  model_kind text NOT NULL CHECK (model_kind IN ('BASELINE','TRAINED')),
  sha256 text NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
  config jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE yta.data_snapshots (
  hash text PRIMARY KEY CHECK (hash ~ '^[a-f0-9]{64}$'),
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH','REPLAY','DEMO')),
  provider text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (payload->>'data_mode' = data_mode),
  CHECK (payload->>'provider' = provider)
);

CREATE TABLE yta.prediction_drafts (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES yta.users(id),
  data_snapshot_hash text NOT NULL REFERENCES yta.data_snapshots(hash),
  model_version text NOT NULL REFERENCES yta.model_versions(id),
  forecast jsonb NOT NULL,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(id, owner_id),
  CHECK (forecast->>'data_snapshot_hash' = data_snapshot_hash),
  CHECK (forecast->>'model_version' = model_version)
);

CREATE TABLE yta.predictions (
  id uuid PRIMARY KEY,
  owner_id uuid NOT NULL REFERENCES yta.users(id),
  draft_id uuid NOT NULL UNIQUE,
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH','REPLAY','DEMO')),
  data_snapshot_hash text NOT NULL REFERENCES yta.data_snapshots(hash),
  model_version text NOT NULL REFERENCES yta.model_versions(id),
  as_of timestamptz NOT NULL,
  created_at timestamptz NOT NULL,
  base_session date NOT NULL,
  target_session date NOT NULL CHECK (target_session > base_session),
  forecast jsonb NOT NULL,
  FOREIGN KEY (draft_id, owner_id) REFERENCES yta.prediction_drafts(id, owner_id),
  CHECK (created_at >= as_of),
  CHECK (forecast->>'data_mode' = data_mode),
  CHECK (forecast->>'data_snapshot_hash' = data_snapshot_hash),
  CHECK (forecast->>'model_version' = model_version),
  CHECK (forecast->>'symbol' = 'NVDA'),
  CHECK ((forecast->>'horizon_sessions')::integer = 5),
  CHECK ((forecast->>'expected_return')::numeric >= -1),
  CHECK (forecast->>'direction' IN ('UP','DOWN','FLAT')),
  CHECK (forecast->>'protocol_version' = 'eod-price-v1')
);
CREATE INDEX predictions_owner_created ON yta.predictions(owner_id, created_at DESC, id);
CREATE INDEX predictions_due ON yta.predictions(target_session, data_mode);

CREATE TABLE yta.idempotency_records (
  owner_id uuid NOT NULL REFERENCES yta.users(id),
  operation text NOT NULL,
  key text NOT NULL CHECK (length(key) BETWEEN 1 AND 128),
  request_hash text NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
  prediction_id uuid NOT NULL REFERENCES yta.predictions(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(owner_id, operation, key)
);

CREATE TABLE yta.prediction_events (
  sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  prediction_id uuid NOT NULL REFERENCES yta.predictions(id),
  type text NOT NULL CHECK (type IN (
    'FROZEN','WAITING_TARGET','WAITING_DATA','SETTLED','UNSCORABLE','SETTLEMENT_FAILED'
  )),
  dedupe_key text NOT NULL,
  payload jsonb NOT NULL,
  occurred_at timestamptz NOT NULL,
  UNIQUE(prediction_id, dedupe_key)
);

CREATE TABLE yta.prediction_outcomes (
  id uuid PRIMARY KEY,
  prediction_id uuid NOT NULL REFERENCES yta.predictions(id),
  settlement_version integer NOT NULL CHECK (settlement_version > 0),
  input_hash text NOT NULL CHECK (input_hash ~ '^[a-f0-9]{64}$'),
  data_snapshot_hash text NOT NULL REFERENCES yta.data_snapshots(hash),
  status text NOT NULL CHECK (status IN ('SETTLED','UNSCORABLE')),
  result jsonb NOT NULL,
  observed_at timestamptz NOT NULL,
  UNIQUE(prediction_id, settlement_version),
  UNIQUE(prediction_id, input_hash),
  CHECK (result->>'status' = status),
  CHECK (
    (status = 'SETTLED' AND result ? 'actual_return') OR
    (status = 'UNSCORABLE' AND length(result->>'reason') > 0)
  )
);

CREATE TABLE yta.audit_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor_id uuid REFERENCES yta.users(id),
  action text NOT NULL,
  resource_id text NOT NULL,
  payload jsonb NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION yta.reject_fact_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'YTA facts are append-only: %.%', TG_TABLE_SCHEMA, TG_TABLE_NAME
    USING ERRCODE = '55000';
END;
$$;

DO $$
DECLARE fact_table text;
BEGIN
  FOREACH fact_table IN ARRAY ARRAY[
    'model_versions','data_snapshots','prediction_drafts','predictions',
    'idempotency_records','prediction_events','prediction_outcomes','audit_events'
  ] LOOP
    EXECUTE format(
      'CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.%I '
      'FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation()', fact_table
    );
  END LOOP;
END;
$$;

