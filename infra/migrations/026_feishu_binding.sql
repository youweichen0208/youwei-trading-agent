ALTER TABLE feishu_login_challenges ADD COLUMN binding_owner_id uuid REFERENCES users(id);
ALTER TABLE feishu_login_challenges ADD COLUMN confirmed_app_id text;
ALTER TABLE feishu_login_challenges ADD COLUMN confirmed_open_id text;
