ALTER TABLE tasks ADD COLUMN artifact_order uuid[] NOT NULL DEFAULT '{}';
UPDATE tasks t SET artifact_order=(SELECT coalesce(array_agg(a.id ORDER BY a.id),'{}') FROM task_artifacts a WHERE a.root_id=t.id AND NOT a.removed)
 WHERE t.parent_id IS NULL;
UPDATE tasks t SET artifact_order=(SELECT coalesce(array_agg((a.value->>'artifactId')::uuid ORDER BY a.ordinality),'{}')
 FROM jsonb_array_elements(w.snapshot->'artifacts') WITH ORDINALITY a(value,ordinality))
 FROM workspaces w WHERE t.workspace_id=w.id AND t.workspace_revision=w.revision AND t.parent_id IS NULL;
