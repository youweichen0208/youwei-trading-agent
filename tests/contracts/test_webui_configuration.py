import json
import sqlite3
import pytest
from integrations.openwebui.configure_assistant import MODEL, configure


def webui_db(path):
    with sqlite3.connect(path) as db:
        db.executescript('''
          CREATE TABLE user(id TEXT, role TEXT);
          INSERT INTO user VALUES ('owner','admin');
          CREATE TABLE config(id INTEGER PRIMARY KEY,data TEXT, version INTEGER, updated_at TEXT);
          INSERT INTO config VALUES (1,'{"unrelated":true}',0,NULL);
          CREATE TABLE function(id TEXT,is_active INTEGER,is_global INTEGER);
          INSERT INTO function VALUES ('old-pipe',1,1);
          CREATE TABLE chat(id TEXT,chat TEXT);
          INSERT INTO chat VALUES ('old','retained');
          CREATE TABLE model(id TEXT PRIMARY KEY,user_id TEXT,base_model_id TEXT,name TEXT,
            params TEXT,meta TEXT,access_control TEXT,is_active INTEGER,created_at INTEGER,updated_at INTEGER);
        ''')


def test_webui_cutover_preserves_history_and_routes_background_tasks(tmp_path):
    path = tmp_path / 'webui.db'; webui_db(path)
    options = dict(owner_id='owner', assistant_key='assistant-private-key', chat_key='ordinary-chat-key', old_pipe_id='old-pipe')
    configure(path, **options); configure(path, **options)
    with sqlite3.connect(path) as db:
        config = json.loads(db.execute('SELECT data FROM config').fetchone()[0])
        assert config['unrelated'] is True
        assert config['task']['model']['external'] == 'glm-5.3'
        assert 'glm-5.3' in config['openai']['api_configs']['0']['model_ids']
        assert config['ui']['default_models'] == MODEL
        assert config['ui']['enable_signup'] is False
        assert db.execute('SELECT chat FROM chat').fetchone()[0] == 'retained'
        assert db.execute('SELECT is_active FROM function').fetchone()[0] == 0
        assert json.loads(db.execute('SELECT access_control FROM model').fetchone()[0])['read']['user_ids'] == ['owner']
        db.execute("INSERT INTO user VALUES ('second','user')")
    with pytest.raises(ValueError, match='exactly'):
        configure(path, **options)


def test_latest_webui_cutover_preserves_history_and_private_model(tmp_path):
    path = tmp_path / 'webui.db'; webui_db(path)
    with sqlite3.connect(path) as db:
        db.executescript('''
          DROP TABLE config;
          CREATE TABLE config(key TEXT PRIMARY KEY, value JSON NOT NULL, updated_at BIGINT);
          INSERT INTO config VALUES ('unrelated', 'true', 1);
          ALTER TABLE model DROP COLUMN access_control;
          CREATE TABLE access_grant(id TEXT PRIMARY KEY, resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL, principal_type TEXT NOT NULL, principal_id TEXT NOT NULL,
            permission TEXT NOT NULL, created_at BIGINT NOT NULL,
            UNIQUE(resource_type,resource_id,principal_type,principal_id,permission));
        ''')
        db.execute('INSERT INTO access_grant VALUES (?,?,?,?,?,?,?)',
                   ('public', 'model', MODEL, 'user', '*', 'read', 0))
        db.execute('INSERT INTO access_grant VALUES (?,?,?,?,?,?,?)',
                   ('other', 'model', 'unrelated-model', 'user', '*', 'read', 0))
    options = dict(owner_id='owner', assistant_key='assistant-private-key',
                   chat_key='ordinary-chat-key', old_pipe_id='old-pipe')
    configure(path, **options); configure(path, **options)
    with sqlite3.connect(path) as db:
        config = {k: json.loads(v) for k,v in db.execute('SELECT key,value FROM config')}
        assert config['unrelated'] is True
        assert config['ui.enable_signup'] is False
        assert config['ui.default_models'] == MODEL
        assert config['task.model.external'] == 'glm-5.3'
        assert config['openai.api_keys'] == ['ordinary-chat-key', 'assistant-private-key']
        assert 'glm-5.3' in config['openai.api_configs']['0']['model_ids']
        assert db.execute('SELECT chat FROM chat').fetchone()[0] == 'retained'
        assert db.execute('SELECT is_active FROM function').fetchone()[0] == 0
        assert db.execute('SELECT user_id FROM model WHERE id=?', (MODEL,)).fetchone()[0] == 'owner'
        assert set(db.execute('SELECT principal_type,principal_id,permission FROM access_grant WHERE resource_id=?', (MODEL,))) == {('user','owner','read'),('user','owner','write')}
        assert db.execute('SELECT count(*) FROM access_grant WHERE id="other"').fetchone()[0] == 1
        db.execute("INSERT INTO user VALUES ('second','user')")
    with pytest.raises(ValueError, match='exactly'):
        configure(path, **options)
