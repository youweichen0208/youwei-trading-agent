-- Pause only accounts already bound when this repair is applied.
INSERT INTO research_settings(owner_id,payload)
SELECT DISTINCT owner_id,'{"dailyCandidateLimit":0}'::jsonb FROM feishu_identities
ON CONFLICT(owner_id) DO UPDATE SET payload=research_settings.payload || excluded.payload;
UPDATE research_coverage SET paused=true
WHERE tier='candidate' AND owner_id IN (SELECT owner_id FROM feishu_identities);
