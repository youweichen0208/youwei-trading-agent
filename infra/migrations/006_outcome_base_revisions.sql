CREATE TABLE yta.outcome_base_revisions (
  outcome_id uuid PRIMARY KEY REFERENCES yta.prediction_outcomes(id),
  snapshot_hash text NOT NULL REFERENCES yta.data_snapshots(hash)
);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.outcome_base_revisions
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
