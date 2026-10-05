"""Offline, reversible Hermes presentation toggle. Stop WebUI and back up first.

Does not touch keys, model grants, background models, chats or the database schema.
Only use after validating a WebUI image with the hermes_chat adapter.
"""
import argparse
import json
import sqlite3
import time


def configure_rich_chat(database, *, owner_id, enabled):
    with sqlite3.connect(database) as db:
        db.execute('BEGIN IMMEDIATE')
        if not owner_id or db.execute('SELECT id FROM user WHERE id=? AND role=?', (owner_id, 'admin')).fetchall() != [(owner_id,)]:
            raise ValueError('existing owner/admin ID required')
        dotted = 'key' in {r[1] for r in db.execute('PRAGMA table_info(config)')}
        if dotted:
            settings = {key: json.loads(value) for key, value in db.execute(
                "SELECT key,value FROM config WHERE key IN ('openai.api_configs','openai.api_base_urls')")}
            configs, urls = settings['openai.api_configs'], settings['openai.api_base_urls']
        else:
            rows = db.execute('SELECT id,data FROM config').fetchall()
            if len(rows) != 1:
                raise ValueError('expected one WebUI configuration')
            row_id, raw = rows[0]
            settings = json.loads(raw)
            configs, urls = settings['openai']['api_configs'], settings['openai']['api_base_urls']
        matches = [str(i) for i, url in enumerate(urls) if url.rstrip('/') == 'http://hermes-assistant:8642/v1'
                   and configs.get(str(i), {}).get('model_ids') == ['Hermes 美股助手']]
        if len(matches) != 1:
            raise ValueError('expected exactly the registered Hermes connection')
        connection = configs[matches[0]]
        if enabled:
            connection.update(hermes_chat=True, hermes_owner_id=owner_id)
        else:
            connection.pop('hermes_chat', None)
            connection.pop('hermes_owner_id', None)
        if dotted:
            db.execute("UPDATE config SET value=?,updated_at=? WHERE key='openai.api_configs'",
                       (json.dumps(configs, ensure_ascii=False), int(time.time())))
        else:
            db.execute('UPDATE config SET data=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                       (json.dumps(settings, ensure_ascii=False), row_id))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--owner-id', required=True)
    parser.add_argument('--enabled', choices=['true', 'false'], required=True)
    args = parser.parse_args()
    configure_rich_chat(args.database, owner_id=args.owner_id, enabled=args.enabled == 'true')
    print('Hermes chat presentation updated. No chats or credentials changed.')
