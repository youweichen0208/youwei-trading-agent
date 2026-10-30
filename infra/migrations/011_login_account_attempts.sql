-- Accepted password-check slots by normalized email, whether an account exists or not.
-- Only a domain-separated hash is stored; rejected requests do not extend the window.
CREATE TABLE yta.login_account_attempts (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  account_hash text NOT NULL CHECK (account_hash ~ '^[a-f0-9]{64}$'),
  attempted_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX login_account_attempts_time ON yta.login_account_attempts(account_hash, attempted_at DESC);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.login_account_attempts
  FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
