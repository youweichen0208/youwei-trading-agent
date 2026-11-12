ALTER TABLE feishu_chats ADD COLUMN context_id uuid;
UPDATE feishu_chats SET context_id=owner_id;
ALTER TABLE feishu_chats ADD FOREIGN KEY(context_id,owner_id) REFERENCES execution_contexts(id,owner_id);
