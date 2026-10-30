-- Accepted password-verification slots, shared by every BFF connected to this database.
-- No password, email, Cookie, token or raw peer address is stored.
CREATE TABLE yta.login_attempts (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  peer_hash text NOT NULL CHECK (peer_hash ~ '^[a-f0-9]{64}$'),
  attempted_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX login_attempts_peer_time ON yta.login_attempts(peer_hash, attempted_at DESC);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.login_attempts
  FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
