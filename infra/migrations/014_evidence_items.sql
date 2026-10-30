-- Content-addressed index for saved MarketSnapshot facts. No supplier qualification implied.
CREATE TABLE yta.evidence_items (
  content_hash text PRIMARY KEY REFERENCES yta.data_snapshots(hash),
  kind text NOT NULL DEFAULT 'MARKET_SNAPSHOT' CHECK (kind = 'MARKET_SNAPSHOT'),
  schema_version text NOT NULL DEFAULT 'market-snapshot-v1' CHECK (schema_version = 'market-snapshot-v1'),
  source_id text NOT NULL CHECK (length(source_id) > 0),
  provider text NOT NULL CHECK (length(provider) > 0),
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH', 'REPLAY', 'DEMO')),
  as_of timestamptz NOT NULL,
  available_at timestamptz NOT NULL CHECK (available_at <= as_of),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX evidence_items_mode_cutoff ON yta.evidence_items(data_mode, as_of, content_hash);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.evidence_items
  FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
