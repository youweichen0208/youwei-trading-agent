CREATE TABLE yta.research_tool_contexts (
  run_id uuid PRIMARY KEY REFERENCES yta.research_runs(id),
  owner_id uuid NOT NULL REFERENCES yta.users(id),
  data_mode text NOT NULL CHECK(data_mode IN ('REPLAY','DEMO','LIVE_RESEARCH')),
  snapshot_hash text NOT NULL REFERENCES yta.data_snapshots(hash),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE yta.research_tool_observations (
  id uuid PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES yta.research_tool_contexts(run_id),
  tool text NOT NULL CHECK(tool IN ('market_snapshot','indicators')),
  content_hash text NOT NULL CHECK(content_hash ~ '^[a-f0-9]{64}$'),
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK((payload->>'schema_version'='research-tool-result-v1') IS TRUE),
  CHECK((payload->>'tool_call_id'=id::text) IS TRUE),
  CHECK((payload->>'context_id'=run_id::text) IS TRUE),
  CHECK((payload->>'tool'=tool) IS TRUE)
);
CREATE INDEX research_tool_observations_run ON yta.research_tool_observations(run_id,created_at,id);
DO $$
DECLARE fact_table text;
BEGIN
  FOREACH fact_table IN ARRAY ARRAY['research_tool_contexts','research_tool_observations'] LOOP
    EXECUTE format('CREATE TRIGGER immutable_fact BEFORE UPDATE OR DELETE OR TRUNCATE ON yta.%I '
      'FOR EACH STATEMENT EXECUTE FUNCTION yta.reject_fact_mutation()',fact_table);
  END LOOP;
END;
$$;
