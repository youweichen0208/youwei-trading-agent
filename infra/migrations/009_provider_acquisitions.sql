-- Candidate acquisition evidence is separate from qualified financial snapshots.
CREATE TABLE yta.provider_acquisitions (
  hash text PRIMARY KEY CHECK (hash ~ '^[a-f0-9]{64}$'),
  provider text NOT NULL CHECK (provider = 'massive'),
  symbol text NOT NULL CHECK (symbol = 'NVDA'),
  data_mode text NOT NULL CHECK (data_mode IN ('LIVE_RESEARCH','REPLAY','DEMO')),
  requested_start date NOT NULL,
  requested_end date NOT NULL CHECK (requested_end >= requested_start),
  qualification_status text NOT NULL CHECK (qualification_status = 'UNVERIFIED'),
  manifest jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE yta.provider_response_pages (
  acquisition_hash text NOT NULL REFERENCES yta.provider_acquisitions(hash),
  kind text NOT NULL CHECK (kind IN ('bars','splits')),
  page_index integer NOT NULL CHECK (page_index >= 0 AND page_index < 10),
  content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
  retrieved_at timestamptz NOT NULL,
  body bytea NOT NULL CHECK (octet_length(body) BETWEEN 1 AND 2097152),
  PRIMARY KEY (acquisition_hash, kind, page_index),
  CHECK (encode(sha256(body), 'hex') = content_hash)
);

CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.provider_acquisitions
  FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.provider_response_pages
  FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation();
