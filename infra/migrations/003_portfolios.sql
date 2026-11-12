CREATE TABLE portfolios (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES users(id), name text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,owner_id)
);
CREATE TABLE portfolio_snapshots (
 id uuid PRIMARY KEY, owner_id uuid NOT NULL, portfolio_id uuid NOT NULL,
 as_of_session date NOT NULL, positions jsonb NOT NULL, cash_usd numeric NOT NULL CHECK(cash_usd>=0),
 created_at timestamptz NOT NULL DEFAULT now(), ordinal bigint GENERATED ALWAYS AS IDENTITY,
 FOREIGN KEY(portfolio_id,owner_id) REFERENCES portfolios(id,owner_id), UNIQUE(id,owner_id)
);
CREATE TRIGGER immutable_holdings BEFORE UPDATE OR DELETE ON portfolio_snapshots
 FOR EACH ROW EXECUTE FUNCTION reject_fact_mutation();
CREATE TABLE watchlist (
 owner_id uuid NOT NULL REFERENCES users(id), symbol text NOT NULL, PRIMARY KEY(owner_id,symbol)
);
ALTER TABLE tasks ADD COLUMN workspace_revision integer;
