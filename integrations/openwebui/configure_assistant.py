"""Offline v0.6.36 / v0.11.4 cutover. Stop WebUI first; retain its data volume.

Credentials are read from env and stored only in WebUI's existing protected DB.
The transaction requires exactly the expected owner account. No chats are edited.
"""
import json
import os
import sqlite3
import time
import uuid

MODEL = 'Hermes 美股助手'


def configure(database, *, owner_id, assistant_key, chat_key, old_pipe_id):
    if not owner_id or min(len(assistant_key), len(chat_key)) < 16 or not old_pipe_id:
        raise ValueError('owner, keys and exact previous Pipe ID required')
    with sqlite3.connect(database) as db:
        db.execute('BEGIN IMMEDIATE')
        users = db.execute('SELECT id, role FROM user').fetchall()
        if users != [(owner_id, 'admin')]:
            raise ValueError('cutover requires exactly the specified owner/admin account')
        # Stable dotted keys in 0.11.4; old 0.6.36 stores a nested JSON blob.
        values = {
            'openai.enable': True,
            'openai.api_base_urls': ['http://litellm:4000/v1', 'http://hermes-assistant:8642/v1'],
            'openai.api_keys': [chat_key, assistant_key],
            'openai.api_configs': {'0': {'enable': True, 'model_ids': ['glm-5.3', 'glm-5.3-flash', 'deepseek-v4-flash', 'deepseek-v4-pro', 'qwen3.8-flash']}, '1': {'enable': True, 'model_ids': [MODEL]}},
            'ui.default_models': MODEL,
            'ui.enable_signup': False,
            # Keep ordinary models discoverable even during gateway discovery failure.
            'task.model.default': 'glm-5.3',
            'task.model.external': 'glm-5.3',
        }
        columns = {r[1] for r in db.execute('PRAGMA table_info(config)')}
        if {'key', 'value', 'updated_at'} <= columns:
            for key, value in values.items():
                db.execute('INSERT INTO config(key,value,updated_at) VALUES (?,?,?) '
                           'ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
                           (key, json.dumps(value, ensure_ascii=False), int(time.time())))
        elif {'id', 'data', 'version', 'updated_at'} <= columns:
            rows = db.execute('SELECT id, data FROM config').fetchall()
            if len(rows) > 1:
                raise ValueError('unexpected multiple WebUI configurations')
            config = json.loads(rows[0][1]) if rows else {}
            for key, value in values.items():
                target = config
                *parents, leaf = key.split('.')
                for parent in parents:
                    target = target.setdefault(parent, {})
                target[leaf] = value
            encoded = json.dumps(config, ensure_ascii=False)
            if rows:
                db.execute('UPDATE config SET data=?, updated_at=CURRENT_TIMESTAMP WHERE id=?', (encoded, rows[0][0]))
            else:
                db.execute('INSERT INTO config (data,version) VALUES (?,0)', (encoded,))
        else:
            raise ValueError('unsupported WebUI config schema; migrate and verify first')
        # Exact ID only. Keep code, valves and chat history for rollback/reference.
        db.execute('UPDATE function SET is_active=0, is_global=0 WHERE id=?', (old_pipe_id,))
        now = int(time.time())
        model_columns = {r[1] for r in db.execute('PRAGMA table_info(model)')}
        fields = ['id', 'user_id', 'base_model_id', 'name', 'params', 'meta', 'is_active', 'created_at', 'updated_at']
        model_values = [MODEL, owner_id, None, MODEL, '{}', '{}', 1, now, now]
        if 'access_control' in model_columns:
            fields.append('access_control')
            model_values.append(json.dumps({'read': {'user_ids': [owner_id], 'group_ids': []},
                                            'write': {'user_ids': [owner_id], 'group_ids': []}}))
        else:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='access_grant'").fetchone():
                raise ValueError('unsupported WebUI model permission schema')
            # Clear this model's old grants only; never alter other resources.
            db.execute("DELETE FROM access_grant WHERE resource_type='model' AND resource_id=?", (MODEL,))
            for permission in ('read', 'write'):
                db.execute('INSERT INTO access_grant '
                           '(id,resource_type,resource_id,principal_type,principal_id,permission,created_at) '
                           'VALUES (?,?,?,?,?,?,?)',
                           (str(uuid.uuid4()), 'model', MODEL, 'user', owner_id, permission, now))
        updates = ','.join(f'{f}=excluded.{f}' for f in fields if f not in ('id', 'created_at'))
        db.execute(f"INSERT INTO model ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)}) "
                   f"ON CONFLICT(id) DO UPDATE SET {updates}", model_values)



if __name__ == '__main__':
    configure(os.environ.get('YOUWEI_WEBUI_DB', '/app/backend/data/webui.db'),
              owner_id=os.environ['YOUWEI_WEBUI_OWNER_ID'],
              assistant_key=os.environ['YOUWEI_ASSISTANT_API_KEY'],
              chat_key=os.environ['YOUWEI_CHAT_KEY'], old_pipe_id=os.environ['YOUWEI_OLD_PIPE_ID'])
    print('Assistant connection configured; chats preserved; background model glm-5.3.')
