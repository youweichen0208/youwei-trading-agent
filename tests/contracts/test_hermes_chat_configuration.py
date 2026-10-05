import json
import sqlite3

import pytest

from integrations.openwebui.configure_hermes_chat import configure_rich_chat


@pytest.mark.parametrize('dotted', [True, False])
def test_rich_chat_toggle_changes_only_hermes_connection_and_can_roll_back(tmp_path, dotted):
    path = tmp_path / 'webui.db'
    configs = {'0': {'model_ids': ['ordinary'], 'api_type': 'chat'},
               '1': {'enable': True, 'model_ids': ['Hermes 美股助手'], 'custom': 'retained'}}
    urls = ['http://litellm:4000/v1', 'http://hermes-assistant:8642/v1']
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE user(id TEXT, role TEXT); INSERT INTO user VALUES ('owner','admin'); CREATE TABLE chat(content TEXT); INSERT INTO chat VALUES ('retained');")
        if dotted:
            db.execute('CREATE TABLE config(key TEXT PRIMARY KEY,value TEXT,updated_at INTEGER)')
            db.executemany('INSERT INTO config VALUES (?,?,0)', [('openai.api_configs',json.dumps(configs)),('openai.api_base_urls',json.dumps(urls)),('task.model.external','"ordinary"')])
        else:
            db.execute('CREATE TABLE config(id INTEGER PRIMARY KEY,data TEXT,updated_at TEXT)')
            db.execute('INSERT INTO config VALUES (1,?,NULL)', (json.dumps({'openai':{'api_configs':configs,'api_base_urls':urls},'task':{'model':{'external':'ordinary'}}}),))
    configure_rich_chat(path, owner_id='owner', enabled=True)
    configure_rich_chat(path, owner_id='owner', enabled=True)
    with sqlite3.connect(path) as db:
        value = json.loads(db.execute("SELECT value FROM config WHERE key='openai.api_configs'").fetchone()[0]) if dotted else json.loads(db.execute('SELECT data FROM config').fetchone()[0])['openai']['api_configs']
        assert value['1']['hermes_chat'] is True
        assert value['1']['hermes_owner_id'] == 'owner'
        assert value['0'] == configs['0']
        assert value['1']['custom'] == 'retained'
        assert db.execute('SELECT content FROM chat').fetchone()[0] == 'retained'
    with pytest.raises(ValueError):
        configure_rich_chat(path, owner_id='other', enabled=True)
    configure_rich_chat(path, owner_id='owner', enabled=False)
    with sqlite3.connect(path) as db:
        value = json.loads(db.execute("SELECT value FROM config WHERE key='openai.api_configs'").fetchone()[0]) if dotted else json.loads(db.execute('SELECT data FROM config').fetchone()[0])['openai']['api_configs']
        assert value == configs
