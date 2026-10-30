-- Append-only symbol reference history. A row is not market-data qualification.
CREATE TABLE yta.symbols (
  id uuid PRIMARY KEY,
  symbol text NOT NULL CHECK (symbol ~ '^[A-Z][A-Z0-9]{0,9}([.-][A-Z0-9]{1,4})?$'),
  version integer NOT NULL CHECK (version > 0),
  status text NOT NULL CHECK (status IN ('UNVERIFIED','ACTIVE','RENAMED','DELISTED')),
  effective_from date,
  effective_to date,
  successor_symbol text CHECK (successor_symbol ~ '^[A-Z][A-Z0-9]{0,9}([.-][A-Z0-9]{1,4})?$'),
  source_id text NOT NULL CHECK (length(source_id) BETWEEN 1 AND 1000),
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(symbol, version),
  CHECK (effective_to IS NULL OR effective_from IS NOT NULL AND effective_to > effective_from),
  CHECK ((status = 'RENAMED' AND successor_symbol IS NOT NULL AND successor_symbol <> symbol)
         OR (status <> 'RENAMED' AND successor_symbol IS NULL)),
  CHECK (status = 'UNVERIFIED' OR effective_from IS NOT NULL)
);
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.symbols
  FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
-- This seed records product scope only, not listing dates or supplier approval.
INSERT INTO yta.symbols(id,symbol,version,status,source_id)
VALUES ('00000000-0000-4000-8000-000000000040','NVDA',1,'UNVERIFIED','yta:mvp-scope');
