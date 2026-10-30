-- Replayed historical simulation reports; not forecasts or model approval.
CREATE TABLE yta.backtest_reports (
  id text PRIMARY KEY,
  data_mode text NOT NULL CHECK (data_mode IN ('REPLAY','DEMO')),
  content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK ((id = 'backtest-report-' || content_hash) IS TRUE),
  CHECK ((payload->>'schema_version' = 'backtest-registration-v1') IS TRUE),
  CHECK ((payload->>'data_mode' = data_mode) IS TRUE),
  CHECK ((payload->'report'->>'data_mode' = data_mode) IS TRUE),
  CHECK ((payload->'report'->>'schema_version' = 'registered-next-open-backtest-report-v2') IS TRUE),
  CHECK ((payload->'report'->>'qualification' = 'UNVERIFIED') IS TRUE),
  CHECK ((payload->'report'->>'metrics_kind' = 'BACKTEST') IS TRUE)
);
CREATE TABLE yta.backtest_report_models (
  report_id text NOT NULL REFERENCES yta.backtest_reports(id),
  model_id text NOT NULL REFERENCES yta.model_versions(id),
  PRIMARY KEY (report_id,model_id)
);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.backtest_reports
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.backtest_report_models
FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
