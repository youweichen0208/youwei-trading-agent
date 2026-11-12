CREATE TABLE research_evidence (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), symbol text NOT NULL,
 source text NOT NULL, source_key text NOT NULL, published_at timestamptz,
 observed_at timestamptz NOT NULL, content_hash text NOT NULL, payload jsonb NOT NULL,
 UNIQUE(id,owner_id), UNIQUE(owner_id,symbol,source,source_key,content_hash)
);
CREATE INDEX research_evidence_lookup ON research_evidence(owner_id,symbol,observed_at DESC);
CREATE TRIGGER immutable_research_evidence BEFORE UPDATE OR DELETE ON research_evidence
 FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
CREATE TABLE research_contexts (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), symbol text NOT NULL,
 as_of timestamptz NOT NULL, payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,owner_id)
);
CREATE TRIGGER immutable_research_contexts BEFORE UPDATE OR DELETE ON research_contexts
 FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
CREATE TABLE research_collection_runs (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), symbol text NOT NULL,
 operation_key text NOT NULL, request_hash text NOT NULL, status text NOT NULL,
 payload jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,operation_key), UNIQUE(id,owner_id)
);
CREATE TABLE research_coverage (
 owner_id uuid NOT NULL REFERENCES users(id), symbol text NOT NULL,
 tier text NOT NULL CHECK(tier IN ('holding','watchlist','temporary','candidate')),
 priority integer NOT NULL DEFAULT 0, paused boolean NOT NULL DEFAULT false, expires_at timestamptz, expires_session date,
 last_collected_at timestamptz, last_analyzed_at timestamptz, last_collection_id uuid,
 updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(owner_id,symbol)
);
CREATE TABLE research_settings (
 owner_id uuid PRIMARY KEY REFERENCES users(id), payload jsonb NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE market_scan_runs (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), session date NOT NULL,
 attempt integer NOT NULL DEFAULT 1 CHECK(attempt>0),
 payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,session,attempt), UNIQUE(id,owner_id)
);
CREATE TRIGGER immutable_market_scan BEFORE UPDATE OR DELETE ON market_scan_runs
 FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
