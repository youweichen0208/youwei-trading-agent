CREATE TABLE yta.settlement_evidence (
  hash text PRIMARY KEY CHECK (hash ~ '^[a-f0-9]{64}$'),
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH','REPLAY','DEMO')),
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (payload->>'data_mode' = data_mode)
);
CREATE TABLE yta.outcome_evidence (
  outcome_id uuid PRIMARY KEY REFERENCES yta.prediction_outcomes(id),
  evidence_hash text NOT NULL REFERENCES yta.settlement_evidence(hash)
);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.settlement_evidence
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.outcome_evidence
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
ALTER TABLE yta.settlement_attempts DROP CONSTRAINT settlement_attempts_status_check;
ALTER TABLE yta.settlement_attempts DROP CONSTRAINT settlement_attempts_check1;
ALTER TABLE yta.settlement_attempts ADD CONSTRAINT settlement_attempt_status
CHECK (status IN ('SETTLED','UNSCORABLE','WAITING_DATA','SETTLEMENT_FAILED'));
ALTER TABLE yta.settlement_attempts ADD CONSTRAINT settlement_attempt_retry
CHECK ((status IN ('SETTLED','UNSCORABLE') AND next_attempt_at IS NULL) OR
       (status IN ('WAITING_DATA','SETTLEMENT_FAILED') AND next_attempt_at > finished_at));
