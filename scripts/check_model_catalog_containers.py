"""Verify built experimental Alpha/BFF images against the dedicated local test DB.

Requires yta-dev_default and yta_test with a registered REPLAY calibration fixture.
Creates only disposable containers and synthetic login facts; no model promotion.
"""

import hashlib
import json
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def command(*args, input=None):
    result = subprocess.run(args, input=input, text=True, capture_output=True, timeout=90)
    if result.returncode:
        # Docker errors can include command/environment details. Do not echo them.
        raise RuntimeError("Container verification command failed")
    return result.stdout.strip()


ALPHA_CHECK = r"""
import hashlib,importlib,json,os
from datetime import datetime,timezone
from urllib.request import Request,urlopen
from yta_alpha.calendar import TradingCalendar
from yta_alpha.calibrate_cli import CalibrationInput
from yta_alpha.errors import DomainError
from yta_alpha.experimental_predictions import ExperimentalRequest,experimental_predict
from yta_alpha.model_registry import ModelRegistry
from pathlib import Path
registry=ModelRegistry(os.environ['YTA_DATABASE_URL'])
assert registry.url.endswith('/yta_test')
with registry._connect() as conn:
    row=conn.execute(
        "SELECT model_version_id,payload FROM yta.calibration_records "
        "WHERE data_mode='REPLAY' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
assert row is not None, 'A registered synthetic REPLAY calibration fixture is required'
spec=CalibrationInput.model_validate(row['payload']['input'])
snapshot=spec.snapshots[0]
model_id=row['model_version_id']
body=ExperimentalRequest(snapshot=snapshot,horizon_sessions=5)
calendar=TradingCalendar()
try:
    experimental_predict(registry,model_id,body,calendar=calendar,now=snapshot.as_of,replay=False)
except DomainError as error:
    assert error.code=='MODEL_NOT_AVAILABLE'
else:
    raise AssertionError('Non-replay inference accepted a model before registration')
expected=experimental_predict(registry,model_id,body,calendar=calendar,now=datetime.now(timezone.utc),replay=True).model_dump(mode='json')
request=Request(
    'http://127.0.0.1:8100/internal/v1/experimental-models/'+model_id+'/predict',
    data=body.model_dump_json().encode(),
    headers={'Authorization':'Bearer '+os.environ['YTA_ALPHA_TOKEN'],
             'Content-Type':'application/json'})
with urlopen(request,timeout=30) as response:
    assert response.status==200
    assert json.load(response)==expected
modules={}
for name in ['api','model_catalog','model_registry','experimental_predictions']:
    module=importlib.import_module('yta_alpha.'+name)
    modules[name]=hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
print(json.dumps({'nonreplay_before_registration':'MODEL_NOT_AVAILABLE','replay_http':200,'native_inference_matches':True,'installed_modules':modules}))
"""

BFF_CHECK = r"""
import assert from 'node:assert/strict';
import {randomBytes,randomUUID} from 'node:crypto';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {database} from './dist/database.js';
import {hashPassword} from './dist/auth.js';
const pool=database(process.env.YTA_DATABASE_URL);
assert(process.env.YTA_DATABASE_URL.endsWith('/yta_test'));
const base='http://127.0.0.1:3100/api/yta/v1';
let cookie,csrf;
try {
  const id=randomUUID(),email=id+'@yta.test',password=randomBytes(24).toString('hex');
  await pool.query('INSERT INTO yta.users(id,email) VALUES($1,$2)',[id,email]);
  await pool.query('INSERT INTO yta.login_credentials(owner_id,password_hash) VALUES($1,$2)',
    [id,await hashPassword(password)]);
  assert.equal((await fetch(base+'/experimental-models')).status,401);
  const login=await fetch(base+'/auth/login',{method:'POST',headers:{Origin:'http://localhost:3000','Content-Type':'application/json'},body:JSON.stringify({email,password})});
  assert.equal(login.status,200);
  cookie=login.headers.get('set-cookie').split(';')[0];csrf=(await login.json()).csrf_token;
  const query='?limit=2&horizon_sessions=5';
  const response=await fetch(base+'/experimental-models'+query,{headers:{Cookie:cookie}});
  assert.equal(response.status,200);
  const page=await response.json();
  const direct=await fetch(process.env.YTA_ALPHA_URL+'/internal/v1/experimental-models'+query,
    {headers:{Authorization:'Bearer '+process.env.YTA_ALPHA_TOKEN}});
  assert.equal(direct.status,200);assert.deepEqual(page,await direct.json());
  assert.equal(page.items.length,2);assert(page.next_cursor);
  const next=await fetch(base+'/experimental-models'+query+'&cursor='+page.next_cursor,
    {headers:{Cookie:cookie}});
  assert.equal(next.status,200);
  const second=await next.json();
  assert(second.items.length>0);
  assert(second.items.every(item=>!page.items.some(prior=>prior.id===item.id)));
  assert.equal((await fetch(base+'/experimental-models?data_mode=LIVE_RESEARCH',
    {headers:{Cookie:cookie}})).status,422);
  const hashes={};
  for(const name of ['app','alpha','config']) {
    hashes[name]=createHash('sha256').update(readFileSync('./dist/'+name+'.js')).digest('hex');
  }
  console.log(JSON.stringify({anonymous:401,authenticated:200,alpha_response_equal:true,second_page_disjoint:true,client_mode_override:422,installed_modules:hashes}));
} finally {
  if(cookie) {
    const response=await fetch(base+'/auth/logout',{method:'POST',headers:{Cookie:cookie,Origin:'http://localhost:3000','X-CSRF-Token':csrf,'Content-Type':'application/json'},body:'{}'});
    assert.equal(response.status,200);
  }
  await pool.end();
}
"""


def main():
    identity = uuid4().hex[:12]
    alpha, bff = f"yta-model-alpha-{identity}", f"yta-model-bff-{identity}"
    containers = []
    result = {}
    try:
        with tempfile.TemporaryDirectory(prefix="yta-model-probe-") as directory:
            env = Path(directory) / "probe.env"
            env.write_text(
                "YTA_ENV=test\nYTA_DATA_MODE=REPLAY\nYTA_EXPERIMENTAL_MODELS=ENABLED\n"
                "YTA_DATABASE_URL=postgresql://yta_app:local-yta-app@postgres:5432/yta_test\n"
                f"YTA_ALPHA_TOKEN={secrets.token_hex(32)}\n"
                f"YTA_ALPHA_URL=http://{alpha}:8100\n"
                "YTA_WEB_ORIGIN=http://localhost:3000\nYTA_BFF_HOST=0.0.0.0\n"
            )
            env.chmod(0o600)
            for name, image in ((alpha, "yta-alpha-training:local"), (bff, "yta-bff:local")):
                # Exercise the packaged HTTP application without recovering unrelated test runs.
                entry = (
                    []
                    if name == alpha
                    else [
                        "node",
                        "--input-type=module",
                        "-e",
                        "import {createBff} from './dist/app.js';"
                        "const s=await createBff({startRunner:false});"
                        "await s.app.listen(3100,'0.0.0.0');"
                        "process.once('SIGTERM',()=>s.close().then(()=>process.exit(0)));",
                    ]
                )
                command(
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--network",
                    "yta-dev_default",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--tmpfs",
                    "/tmp",
                    "--memory",
                    "512m",
                    "--cpus",
                    "1",
                    "--env-file",
                    str(env),
                    image,
                    *entry,
                )
                containers.append(name)
            for name, program, script in (
                (
                    alpha,
                    "python",
                    "from urllib.request import urlopen;urlopen('http://127.0.0.1:8100/health',timeout=2)",
                ),
                (
                    bff,
                    "node",
                    "fetch('http://127.0.0.1:3100/api/yta/v1/health').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))",
                ),
            ):
                for _ in range(30):
                    try:
                        command(
                            "docker",
                            "exec",
                            name,
                            program,
                            "-c" if program == "python" else "-e",
                            script,
                        )
                        break
                    except RuntimeError:
                        time.sleep(0.2)
                else:
                    raise RuntimeError("Container did not become healthy")
            result["alpha"] = json.loads(
                command("docker", "exec", alpha, "python", "-c", ALPHA_CHECK)
            )
            result["bff"] = json.loads(
                command("docker", "exec", bff, "node", "--input-type=module", "-e", BFF_CHECK)
            )
            for name, section in ((alpha, "alpha"), (bff, "bff")):
                state = json.loads(command("docker", "inspect", name))[0]
                assert state["HostConfig"]["ReadonlyRootfs"]
                assert not state["HostConfig"]["PortBindings"]
                assert state["Config"]["User"] in ("node", "10001:10001")
                assert "ALL" in state["HostConfig"]["CapDrop"]
                result[section].update(
                    image=state["Image"],
                    user=state["Config"]["User"],
                    read_only=True,
                    published_ports=False,
                )
            for name, digest in result["alpha"]["installed_modules"].items():
                path = ROOT / "services/alpha-engine/src/yta_alpha" / f"{name}.py"
                assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
            for name, digest in result["bff"]["installed_modules"].items():
                path = ROOT / "apps/bff/dist" / f"{name}.js"
                assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    finally:
        for name in reversed(containers):
            command("docker", "rm", "-f", name)
    result.update(
        scope="Local Linux containers and synthetic REPLAY only",
        containers_cleaned=True,
        bff_research_runner=False,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
