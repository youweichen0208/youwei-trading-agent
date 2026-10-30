"""Fresh PostgreSQL Hermes admission tests, immutable guards and actual backup/restore."""

import json
import os
import subprocess
from uuid import uuid4

import backup_local as backup
import psycopg
from psycopg import sql
from yta_alpha.migrate import migrate

CHECK = """
import assert from 'node:assert/strict';
import {database} from './apps/bff/dist/database.js';
import {HermesStore} from './apps/bff/dist/hermes-store.js';
import {ResearchRepository} from './apps/bff/dist/research-store.js';
const pool=database(process.env.YTA_TEST_DATABASE_URL);
try {
 const store=new HermesStore(pool,'REPLAY'),repo=new ResearchRepository(pool,'REPLAY');
 const before=(await pool.query('SELECT count(*)::int AS n FROM yta.audit_events')).rows[0].n;
 const rows=(await pool.query('SELECT * FROM yta.hermes_run_requests ORDER BY run_id')).rows;
 assert.ok(rows.length>=6);let receipts=0,unknown=0,eventCount=0;
 for(const row of rows){
  const run=await repo.getRun(row.owner_id,row.run_id);
  const saved=await store.get(run);assert.ok(saved);
  const claim=await store.claim(run,row.runtime_hash,row.body);
  assert.equal(claim.send,false);assert.deepEqual(claim.request,saved);
  if(saved.upstreamRunId){
   receipts++;
   const checkpoint=await store.checkpoint(run,row.runtime_hash);
   const count=(await pool.query(
    'SELECT count(*)::int AS n FROM yta.hermes_run_events WHERE run_id=$1',[run.id])).rows[0].n;
   assert.equal(checkpoint.cursor,count-1);eventCount+=count;
  }else unknown++;
 }
 assert.ok(receipts>0 && unknown>0 && eventCount>0);
 const after=(await pool.query('SELECT count(*)::int AS n FROM yta.audit_events')).rows[0].n;
 assert.equal(after,before);
 console.log(JSON.stringify({requests:rows.length,receipts,unknown,eventCount,no_resend:true,no_new_audit:true}));
}finally{await pool.end();}
"""


ROLLBACK = """
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {database} from './apps/bff/dist/database.js';
import {HermesStore} from './apps/bff/dist/hermes-store.js';
import {ResearchRepository} from './apps/bff/dist/research-store.js';
const pool=database(process.env.YTA_TEST_DATABASE_URL);
try {
 const owner=randomUUID(),repo=new ResearchRepository(pool,'REPLAY');
 await pool.query('INSERT INTO yta.users(id,email) VALUES($1,$2)',[owner,owner+'@rollback.test']);
 const session=await repo.createSession(owner,'REPLAY',randomUUID());
 const request=await repo.submit(owner,session.session_id,randomUUID(),'audit rollback','REPLAY');
 const run=await repo.getRun(owner,request.run_id),store=new HermesStore(pool,'REPLAY');
 await assert.rejects(store.claim(run,'a'.repeat(64),{input:run.prompt,model:'pi-selected'}),
   {code:'P0001'});
 assert.equal(await store.get(run),null);
 const bindings=await pool.query('SELECT * FROM yta.hermes_session_bindings WHERE owner_id=$1',
   [owner]);
 assert.equal(bindings.rowCount,0);
 await repo.append(run,{type:'run.canceled',payload:{}});
}finally{await pool.end();}
"""


EVENT_ROLLBACK = """
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {database} from './apps/bff/dist/database.js';
import {HermesStore} from './apps/bff/dist/hermes-store.js';
import {ResearchRepository} from './apps/bff/dist/research-store.js';
const pool=database(process.env.YTA_TEST_DATABASE_URL);
try {
 const owner=randomUUID(),repo=new ResearchRepository(pool,'REPLAY');
 await pool.query('INSERT INTO yta.users(id,email) VALUES($1,$2)',[owner,owner+'@events.test']);
 const session=await repo.createSession(owner,'REPLAY',randomUUID());
 const request=await repo.submit(owner,session.session_id,randomUUID(),'event rollback','REPLAY');
 const run=await repo.getRun(owner,request.run_id),store=new HermesStore(pool,'REPLAY');
 const hash='a'.repeat(64),id='run_'+randomUUID().replaceAll('-','');
 await store.claim(run,hash,{input:run.prompt,model:'pi-selected'});
 await store.receipt(run,hash,id);
 await assert.rejects(store.ingest(run,hash,
   {run_id:id,event:'message.delta',seq:0,delta:'must roll back'}),{code:'P0001'});
 assert.equal((await store.checkpoint(run,hash)).cursor,-1);
 assert.equal((await repo.events(owner,run.id,0)).length,0);
 await repo.append(run,{type:'run.canceled',payload:{}});
}finally{await pool.end();}
"""


def main():
    source = f"yta_backup_fixture_{uuid4().hex}_test"
    with backup.connect("postgres", autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(source)))
        try:
            migrate(backup.LOCAL + " dbname=" + source, backup.ROOT / "infra/migrations")
            with backup.connect(source) as connection:
                connection.execute("GRANT USAGE ON SCHEMA yta TO yta_app")
                connection.execute("GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA yta TO yta_app")
                connection.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA yta TO yta_app")
            env = {
                **os.environ,
                "YTA_TEST_DATABASE_URL": f"postgresql://yta_app:local-yta-app@127.0.0.1:54329/{source}",
            }
            log = backup.ROOT / ".local" / f"{source}.log"
            with backup.private_file(log) as output:
                subprocess.run(
                    [
                        "pnpm",
                        "--filter",
                        "@yta/bff",
                        "exec",
                        "vitest",
                        "run",
                        "tests/hermes-store.integration.test.ts",
                    ],
                    cwd=backup.ROOT,
                    env=env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=90,
                    check=True,
                )
            with backup.connect(source) as connection:
                connection.execute("""
                    CREATE FUNCTION yta.probe_hermes_audit_failure() RETURNS trigger
                    LANGUAGE plpgsql AS $$ BEGIN
                      IF NEW.action='hermes.request_claimed' THEN
                        RAISE EXCEPTION 'synthetic audit failure';
                      END IF;
                      RETURN NEW;
                    END $$;
                    CREATE TRIGGER probe_hermes_audit BEFORE INSERT ON yta.audit_events
                    FOR EACH ROW EXECUTE FUNCTION yta.probe_hermes_audit_failure();
                """)
            subprocess.run(
                ["node", "--input-type=module", "-e", ROLLBACK],
                cwd=backup.ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            with backup.connect(source) as connection:
                connection.execute("DROP TRIGGER probe_hermes_audit ON yta.audit_events")
                connection.execute("DROP FUNCTION yta.probe_hermes_audit_failure()")
            with backup.connect(source) as connection:
                connection.execute("""
                    CREATE FUNCTION yta.probe_hermes_event_failure() RETURNS trigger
                    LANGUAGE plpgsql AS $$ BEGIN
                      RAISE EXCEPTION 'synthetic ledger failure';
                    END $$;
                    CREATE TRIGGER probe_hermes_event BEFORE INSERT ON yta.hermes_run_events
                    FOR EACH ROW EXECUTE FUNCTION yta.probe_hermes_event_failure();
                """)
            subprocess.run(
                ["node", "--input-type=module", "-e", EVENT_ROLLBACK],
                cwd=backup.ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            with backup.connect(source) as connection:
                connection.execute("DROP TRIGGER probe_hermes_event ON yta.hermes_run_events")
                connection.execute("DROP FUNCTION yta.probe_hermes_event_failure()")
            guards = 0
            for table in (
                "hermes_session_bindings",
                "hermes_run_requests",
                "hermes_run_receipts",
                "hermes_run_events",
            ):
                pk = "session_id" if table == "hermes_session_bindings" else "run_id"
                for query in (
                    f"UPDATE yta.{table} SET {pk}={pk}",
                    f"DELETE FROM yta.{table}",
                    f"TRUNCATE yta.{table} CASCADE",
                ):
                    try:
                        with backup.connect(source) as connection:
                            connection.execute(query)
                    except psycopg.Error as error:
                        if error.sqlstate != "55000":
                            raise
                        guards += 1
                    else:
                        raise RuntimeError("Owner mutated a Hermes fact")
            archive, manifest = backup.backup(source, backup.ROOT / ".local/backups")
            restored = backup.restore_drill(archive, manifest)
            env["YTA_TEST_DATABASE_URL"] = (
                "postgresql://yta_app:local-yta-app@127.0.0.1:54329/"
                + restored["restored_database"]
            )
            result = subprocess.run(
                ["node", "--input-type=module", "-e", CHECK],
                cwd=backup.ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            print(
                json.dumps(
                    {
                        "owner_guards": guards,
                        "audit_rollback": True,
                        "projection_rollback": True,
                        "test_log": str(log.relative_to(backup.ROOT)),
                        "verification": restored,
                        "hermes": json.loads(result.stdout),
                        "scope": "Fresh local Hermes facts, not production restore",
                    }
                )
            )
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(source)))


if __name__ == "__main__":
    main()
