ALTER TABLE threads ADD COLUMN source text NOT NULL DEFAULT 'web' CHECK(source IN ('web','feishu_private','feishu_group'));
UPDATE threads t SET source='feishu_private' WHERE EXISTS(SELECT 1 FROM feishu_chats c WHERE c.thread_id=t.id);
UPDATE threads t SET source='feishu_group' FROM thread_contexts tc JOIN execution_contexts c ON c.id=tc.context_id WHERE tc.thread_id=t.id AND c.kind='group';
