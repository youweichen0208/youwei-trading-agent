-- Formatted document body for the account notebook. `body` stays the plain-text
-- mirror the agent reads, so formatting never becomes prompt noise.
ALTER TABLE personal_notes ADD COLUMN body_html text
  CHECK (body_html IS NULL OR length(body_html) BETWEEN 1 AND 200000);
