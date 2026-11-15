ALTER TABLE threads ADD COLUMN deleted_at timestamptz;
CREATE INDEX threads_active ON threads(owner_id, created_at DESC) WHERE deleted_at IS NULL;
