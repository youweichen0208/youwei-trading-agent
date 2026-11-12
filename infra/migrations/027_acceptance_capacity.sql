CREATE TABLE acceptance_capacity (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 context_id uuid NOT NULL REFERENCES execution_contexts(id),
 reservation_id uuid NOT NULL UNIQUE,
 status text NOT NULL CHECK(status IN ('running','unknown')),
 created_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON acceptance_capacity FROM PUBLIC;
