-- Account-level notebook: the user's own style, preferences and reflections.
-- Shared by every Profile of one account (no profile_id); isolation is owner_id only.
CREATE TABLE personal_notes (
 id uuid PRIMARY KEY,
 owner_id uuid NOT NULL REFERENCES users(id),
 title text NOT NULL CHECK(length(title) BETWEEN 1 AND 200),
 body text NOT NULL CHECK(length(body) BETWEEN 1 AND 20000),
 tags text[] NOT NULL DEFAULT '{}'
   CHECK(array_length(tags,1) IS NULL OR array_length(tags,1)<=10),
 symbol text CHECK(symbol IS NULL OR symbol ~ '^[A-Z][A-Z0-9.-]{0,14}$'),
 pinned boolean NOT NULL DEFAULT false,
 use_in_agent boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,owner_id)
);
CREATE INDEX personal_notes_owner_recent ON personal_notes(owner_id,updated_at DESC);
REVOKE ALL ON personal_notes FROM PUBLIC;
