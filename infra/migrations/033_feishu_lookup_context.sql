-- Per sender/chat clarification state, separate from model conversation history.
ALTER TABLE feishu_chats ADD COLUMN lookup_context jsonb;
