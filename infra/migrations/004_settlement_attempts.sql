CREATE TABLE yta.settlement_attempts (
  id uuid PRIMARY KEY,
  prediction_id uuid NOT NULL REFERENCES yta.predictions(id),
  attempt_number integer NOT NULL CHECK (attempt_number > 0),
  status text NOT NULL CHECK (status IN ('SETTLED','WAITING_DATA','SETTLEMENT_FAILED')),
  error_code text,
  started_at timestamptz NOT NULL,
  finished_at timestamptz NOT NULL CHECK (finished_at >= started_at),
  next_attempt_at timestamptz,
  UNIQUE(prediction_id, attempt_number),
  CHECK ((status = 'SETTLED' AND next_attempt_at IS NULL) OR
         (status <> 'SETTLED' AND next_attempt_at > finished_at))
);
CREATE INDEX settlement_attempts_latest ON yta.settlement_attempts(prediction_id, attempt_number DESC);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.settlement_attempts
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
