# Trusted Hermes runtime bridge

This directory contains an Alpha-specific plugin and a guarded source patch for
[Hermes v0.23.1](https://github.com/nousresearch/hermes-agent/tree/3b421578588243f920886e6fe79a6faa87ea026e).
The immutable commit, release image manifest, and normalized source hashes are
recorded in `upstream.json`. These pins identify build inputs, not deployment
evidence. Register the resulting custom image only after isolated acceptance and
the actual target Profiles' contract checks pass. The runtime tests cover native
in-process executor threads, not the optional managed-subprocess policy.
Building the image does not generate passing Worker registry evidence.

### Opt-in live model check

`tests/hermes/provider_integration.py` uses the actual prepared image and three
role containers. Each role calls the configured model, invokes `get_workspace`,
and returns a random nonce supplied only by the test tool. Research also replays
the same completed run after a container restart. The workspace response is a
fixture; this does not certify market data or full research collaboration.

Supply a private `0600` JSON file with one model entry (`name`, `baseUrl`,
`apiMode`, `apiKey`, and optional Anthropic `authMode`). All three roles use that
entry for this check. Requests incur the configured provider's normal usage.

```sh
export ALPHA_MODEL_FILE=/absolute/private/model.json
export HERMES_IMAGE=sha256:<ID returned by docker image inspect alpha-lab-hermes:local>
export TEST_DATABASE_URL=postgresql://.../alpha_test
# On Linux Docker hosts, bind test peers to the host's private Docker gateway:
# export ALPHA_TEST_BIND_HOST=172.17.0.1
uv run pytest -q -s --tb=short tests/hermes/provider_integration.py
```

The test creates and removes only its own Compose project and database schema.
It writes a sanitized `provider-evidence.json` in its private test directory,
without producing a passing production registry. Provider secrets never appear
in command arguments. When reusing Claude Code settings, copy only the selected
endpoint, model and authorized credential into this private model entry; do not
mount the Claude home or enable Hermes' adoption of unrelated host logins.

For the full local research chain, also supply `ALPHA_MARKET_FILE` pointing to a
private `0600` JSON object with `provider: "alpaca"`, `feed: "iex"` or `"sip"`,
`apiKey`, and `apiSecret`. Run `tests/hermes/live_research_integration.py` with
the same model/image/test-database environment. This explicitly invokes both
real providers through the real Worker, private tool gateway, PostgreSQL and
Quant. It checks three role executions, five Canvas component types, recorded
market provenance, specialist results and cross-user rejection. Its simulated
holdings are test inputs; no brokerage or trading endpoint is called. The
test's own Compose project and schema are removed after acceptance. Passing
local research still does not register or certify a production deployment.

## Execution boundary

1. The authenticated private Runs endpoint accepts the Worker's fixed request
   shape and `X-Alpha-Tool-Capability`. Native durable idempotency is required;
   Hermes' fallback to an in-memory ledger is refused.
2. The native request fingerprint includes the capability. A retry under the
   same key with another capability returns a conflict before another admission.
3. Before the run can enter the canonical FIFO, its capability is stored under
   its unique `run_id` in a Profile-private SQLite file. The binding is immutable.
   The file is mode `0600` in a newly created `0700` directory. This file contains
   bearer secrets in plaintext and must live on the Profile's private volume;
   it must never be mounted into another Profile or exposed through file tools.
4. Only the opaque run ID enters the canonical durable admission. It is not a
   session setting, so a subsequent unrelated API turn cannot inherit authority.
   The token is absent from model requests, arguments and conversation memory.
5. Core dispatch establishes a per-call ContextVar from that admission and the
   executor's tool-call ID. Authorization runs outside fail-open plugin
   middleware. The registered handler independently rejects absent context.
6. HTTP calls use a fixed configured gateway origin, a bearer header and a stable
   operation ID derived from run/call IDs. There are no transport retries,
   redirects or environment proxies. Transport errors are sanitized.

The patched tool parser refuses built-ins before inline dispatch or tool-search
expansion. Only the selected role's generated gateway catalog is allowed. The
gateway still validates the execution token, role, ownership and arguments for
every call. Native `delegate_task` is disabled; collaboration uses the app's
`submit_specialist` tool. Runs never hand execution to a Desktop chat owner.
Bootstrap explicitly disables native deferred tool discovery and connectors.
Otherwise Hermes replaces the role catalog with `tool_search`/`tool_call`
wrappers, which the trusted parser rejects before any project tool can run.

Model-facing catalogs expose plain object schemas. Quant advertises only the
current role's calculation variant. Canvas advertises that role's components
and their reference fields, with the conditional reference requirement in the
description. The gateway still validates the exact discriminated union and
role on every call. This separation is required because pinned Hermes drops
top-level schema unions when converting tools to Anthropic, otherwise leaving
these tools without parameter definitions. The source audit runs that native
conversion against the generated catalog to catch this regression.

## Build and configuration

From the repository root:

```sh
pnpm hermes:catalog
docker build -f services/hermes/Dockerfile -t alpha-lab-hermes:local services/hermes
```

The build validates all 35 pinned patch, audit and runtime files before writing
any patch, including the dependency lockfile and canonical admission/recovery
implementation. Missing or changed dependencies fail before the first write; an
unexpected upstream revision fails. The base image's digest is not the digest
of the resulting custom image. Register only the latter after actual deployment
checks pass. The Dockerfile preserves the upstream entrypoint, s6 initialization
and privilege drop. It replaces the native gateway-slot reconciliation hook with
an empty service-directory initializer. Bootstrap runs `gateway run --no-supervise
--external-supervisor` as the container's main program. This preserves its
filtered role/credential environment and makes gateway exit stop the container;
native s6 slots cannot silently restart it or restore another gateway from home.
The host ProfileSupervisor owns subsequent start/recovery decisions.

Before privilege drop, the application initializer validates the mounted runtime
identity against `.alpha-profile.json` and hands that one file to the actual
`hermes` UID/GID, retaining mode `0600`. This is necessary on Linux: the upstream
hook changes the home directory's owner but leaves this custom marker at the
preparing host UID. The initializer opens the home and marker without following
symlinks, refuses nonregular/oversized/multiply linked files, and changes the
validated file descriptor rather than recursively changing home contents.
`S6_BEHAVIOUR_IF_STAGE2_FAILS=2` makes a refused initialization terminate the
container before its main program; s6's default would otherwise continue.

The upstream initialization hook generates an API key in the private home
`.env`, which native dotenv loading prioritizes over process environment.
Bootstrap atomically replaces that managed file with the mounted Profile's
authoritative `API_SERVER_KEY`, mode `0600`, and refuses symlinks. Model provider
keys remain in the filtered process environment, avoiding dotenv interpolation;
they are not written into `config.yaml` or `.env`. Existing memory is preserved.

The private runtime needs:

| Setting | Value |
| --- | --- |
| `ALPHA_ROLE` | `research`, `prediction`, or `portfolio` |
| `ALPHA_BINDING_DB` | Absolute path to a private persistent directory's SQLite file |
| `ALPHA_TOOL_ORIGIN` | Fixed private gateway origin, without credentials or path |
| Hermes plugin configuration | Enable `alpha-lab`; expose only toolset `alpha_lab` |

Do not give the container application database credentials, Market credentials,
the Worker's capability encryption key, another Profile's home, or Docker socket
access. Each Profile still needs independent upstream credentials, home and
memory volume, a restricted API listener, resource limits and an isolated network
configuration. Environment values alone do not establish those guarantees.

### Automatically prepare new registrations

After migration 017, registration creates one durable preparation job in the same
transaction as the user and three Profile identities. A trusted host process with
the **Worker database account** and an operator-supplied private model policy can
prepare these jobs:

```sh
pnpm hermes:prepare-auto PRIVATE_SETTINGS PRIVATE_OUTPUT_ROOT LAST_PORT
# One bounded pass, or explicitly retry one blocked owner:
pnpm hermes:prepare-auto PRIVATE_SETTINGS PRIVATE_OUTPUT_ROOT LAST_PORT --once
pnpm hermes:prepare-auto PRIVATE_SETTINGS PRIVATE_OUTPUT_ROOT LAST_PORT --retry OWNER_UUID
```

Use the settings shape below. `firstPort` and `LAST_PORT` define an inclusive
loopback API port pool, allocated in triples. The automatic output root must be
new/empty and private (`0700`) on first use. Its canonical path, filesystem
identity and private marker are bound to a single immutable policy per database
schema. Model configuration, provider keys, image, root and port pool cannot
silently change on restart. This initial deployment supports **one host and one
port namespace per schema**; moving the root or expanding/changing the policy
requires a separately reviewed migration, not deleting the binding and retrying.

The daemon scans at most 100 queued/preparing owners per pass and polls every five
seconds. Per-owner PostgreSQL session locks serialize preparers and release when
the connection ends. Three individual port reservations prevent overlap with
other automatic jobs and new manual preparations in the same schema. They do not
reserve OS sockets or discover pre-existing deployments/other applications;
choose an unused host range and verify Docker bindings during deployment.
Reservations are not automatically recycled on failure.

Each job has an immutable request ID and a private, atomically published seed in
`.auto-seeds`. The seed retains the three independently generated runtime API
keys. Files are written and flushed in a private staging directory, then the
whole directory is renamed into place. A process crash can leave an unpublished
private staging directory; recovery uses the original seed in a new staging
directory. Already published output is adopted only if every expected file,
permission and identity matches exactly. Missing seeds, changed files, symlinks
or changed roots block preparation; retry never overwrites or rotates credentials.
Keep the root, seeds and database together in backups. Stale staging directories
are not removed automatically, so inspect them before any manual cleanup.

`prepared` means **configuration prepared, unverified**. Profile readiness stays
`unprovisioned`; the preparer does not start Docker, import host logins, generate
passing evidence, register runtimes or bypass the existing deployment checks.
After real validation, use the resulting owner directory with the verified
registry/supervisor workflow. Without a private policy the jobs stay queued;
no default provider credentials or fabricated model results are used. Blocked
jobs require `--retry OWNER_UUID` after their cause is resolved. Old users are
not silently enrolled, since they may already have separately prepared homes.
The manual command remains available and marks its job `manual`, excluding it
from automatic preparation. Use a separate manual output root.

### Prepare a user's three Profiles

`pnpm hermes:prepare OWNER_UUID PRIVATE_SETTINGS PRIVATE_OUTPUT_DIR` reads the
owner's existing three Profile IDs from PostgreSQL. Set the operator's
`DATABASE_URL` in the process environment. The settings file must be a regular
file with mode `0600`; the output directory must be private (`0700`). The command
does not print credentials, start containers, rotate existing keys, generate
passing evidence, or change database readiness.

The settings JSON has this shape (supply all three model entries):

```json
{
  "image": "sha256:<64-character custom image ID>",
  "gatewayOrigin": "http://host.docker.internal:3410",
  "firstPort": 18642,
  "models": {
    "research": {
      "name": "<configured model name>",
      "baseUrl": "https://model.example/v1",
      "apiMode": "chat_completions",
      "apiKey": "<model provider key>"
    },
    "prediction": { "name": "...", "baseUrl": "...", "apiMode": "chat_completions", "apiKey": "..." },
    "portfolio": { "name": "...", "baseUrl": "...", "apiMode": "chat_completions", "apiKey": "..." }
  }
}
```

The bootstrap supports explicitly configured custom providers using
`chat_completions`, `responses`, or `anthropic_messages`. Anthropic-compatible
gateways can also specify `"authMode": "bearer"` or `"authMode": "api_key"`.
Omitting `authMode` preserves Hermes' endpoint-specific authentication selection;
the field is rejected for other protocols. An explicit mode applies only to the
Profile's configured endpoint and credential. The patched native SDK builder
sends one credential header, and credentials remain outside `config.yaml`.
It does not choose a model or
import host logins. Provider keys are supplied independently for each role;
the runtime's own API keys are always separately generated. `gatewayOrigin`
must be reachable from the containers: a host-only listener at `127.0.0.1:3410`
is not assumed to be reachable through Docker's host gateway. Verify the private
network binding before starting research; never publish the tool service to the
public internet just to make this address work.

The output contains one `compose.json`, a private `worker.env`, an unverified
registry and three separate Profile directories. Compose assigns each Profile
its own network, home and memory, read-only runtime specification and credential
file, loopback API port, CPU/memory/PID limits and bounded logs. No host home or
Docker socket is mounted. Credential files are `0444` inside private `0700`
directories so the container's unprivileged UID can read only its mounted file.
Do not relocate those files into a shared/public directory.

The upstream s6 initializer prepares the mounted home and drops privileges. Our
bootstrap refuses root execution, checks owner/Profile/role against the home
marker, writes non-secret Hermes configuration and the private API-key `.env`, then starts the gateway
with an allowlisted environment plus that Profile's keys. Another Profile's
marker, an unmarked home or an identity override fails before configuration is
written. Existing memories and execution bindings are retained across restarts.

Use `docker compose -f /private/output/OWNER_UUID/compose.json config --quiet`
to validate a prepared file. Actual startup uses `up -d` with the same file after
the custom image and private endpoint are available. The registry deliberately
references missing evidence files, so the Worker refuses it until real checks
have been collected. An optional host supervisor can now start and stop prepared, verified Profiles on task demand; see the lifecycle section below. Automatic preparation is available; contract verification and runtime activation still require an operator.

### Activate verified production accounts

A working initial account does not establish readiness for later registrations.
For each new owner, preparation, real contract verification, registry enrollment,
and supervisor enrollment are separate steps. Never copy another owner's evidence
or share its runtime binding.

On the existing `/opt/hermes-alpha-lab` deployment, run
`infra/deployment/check-profiles.py OWNER_DIRECTORY` only in a maintenance window
with no active research and the business tool service stopped: this verifier
binds the tool gateway port and uses private synthetic tool responses with the
real model. Restore the tool service in a shell trap even when verification fails.
Only successful checks create that owner's `profiles.verified.json` and evidence.

Then run as root, passing **all existing and new verified owner directories**:

```sh
python3 infra/deployment/activate-verified-profiles.py \
  /opt/hermes-alpha-lab/private/profiles/EXISTING_OWNER \
  /opt/hermes-alpha-lab/private/profiles/NEW_OWNER
```

The activation command validates all deployments using the actual loader,
refuses to drop existing Profile IDs, copies evidence into the Worker's private
registry, adds each owner's credentials to Worker and supervisor environments,
and restarts those two services. It preserves their release paths and stores
previous units/registries in a timestamped private activation backup.

With the Worker's database, registry, capability and all Profile environment
files loaded, run `node --import tsx infra/deployment/check-owner-runtime.ts OWNER_UUID`.
All three roles must have both `runtimeConnected` and `lifecycleManaged` true.
`stopped` is a valid idle state: verify wake-up with an explicitly authorized
research request, not by changing database readiness directly. Existing failed
tasks remain failed; a retry is a new task with its own idempotency key.

## Verification

```sh
uv run pytest -q tests/hermes
HERMES_SOURCE=/absolute/path/to/pinned/hermes uv run \
  --with ruamel.yaml --with pyyaml --with aiohttp --with python-dotenv \
  pytest -q tests/hermes/audit_upstream.py
```

The first command exercises concurrent real HTTP requests to a controlled local
gateway, immutable bindings, role denial, missing-context denial, stable keys,
redirect refusal, secret-echo redaction and plugin registration. The explicit
audit requires the full pinned source boundary from `files`, `auditFiles` and
`runtimeFiles`, including the hashed SQLite dependencies;
missing or changed source fails rather than skipping. It executes upstream
Runs/admission/parser function bodies with controlled boundaries and the real
native SQLite idempotency store. It verifies reopen/replay, capability conflicts,
pre-admission binding, opaque identity restoration and built-in denial.

The audit also invokes the actual image-build patcher against copies with each
of the 31 unpatched dependencies individually changed. Every mutation must fail
and leave all four patch targets byte-for-byte unchanged; a pristine copy must
produce exactly the expected patches.

### Complete pinned-source processes

Keep an unmodified checkout/archive of the pinned commit and a separate runtime
copy. The project calculator uses Python 3.12; this Hermes revision requires an
isolated Python 3.14 environment. From the repository root, with both paths set
to absolute directories:

```sh
export HERMES_SOURCE=/absolute/path/to/original-pinned-hermes
export HERMES_RUNTIME_SOURCE=/absolute/path/to/separate-runtime-copy
uv sync --project "$HERMES_RUNTIME_SOURCE" --frozen --no-dev --python 3.14 --extra messaging
uv run python services/hermes/apply_patch.py "$HERMES_RUNTIME_SOURCE"
cp -R services/hermes/alpha_hermes "$HERMES_RUNTIME_SOURCE/alpha_hermes"
cp -R services/hermes/plugin "$HERMES_RUNTIME_SOURCE/plugins/alpha-lab"
uv run pytest -q tests/hermes/runtime_integration.py
```

Use fresh destinations for the two `cp` commands. The fixture verifies the
patched files, bridge/plugin copies, runtime source hashes, lockfile and Python
version before launch. It starts the absolute runtime `.venv/bin/hermes` path
with private temporary homes and filtered environments, so another installed
Hermes command or login profile is not used. The controlled model advertises a
128,000-token context; a context below Hermes' 64,000-token minimum is rejected.
The tests verify actual authenticated tool requests, the model-facing role
catalog, unchanged native run IDs on replay, changed-capability conflicts and
research process restart with saved output and no repeated tool call.

For the database/Worker integration, also install the root Node dependencies,
run `uv sync`, and start the dedicated test PostgreSQL instance. Then run:

```sh
TEST_DATABASE_URL=postgresql://alpha:local-development-only@127.0.0.1:55439/alpha_test \
  uv run pytest -q tests/hermes/system_integration.py
```

This opt-in test launches three actual gateways plus a Node peer using the real
TaskWorker, HermesClient, HTTP tool handler, PostgreSQL and Python calculator.
Model decisions and market bars are fixtures. It verifies five persisted Canvas
types, two specialist tasks, multiple tools in one completion, three distinct
native runs/sessions, retired capabilities, audit records, native SSE archival
and foreign-owner read rejection.

A local HTTP fault proxy withholds the first accepted create response. The test
sends SIGKILL to the Worker subprocess, confirms its exit, and starts a new process
running the same production scan/maintenance/lease loop. It keeps the real
30-second lease: replay cannot happen early. The new process must retain the
original request/session/idempotency key/capability and native run, without extra
market operations. Runtime bindings are injected only into a test subprocess;
production still uses the verified registry gate. This tests the service loop in
a real OS process, not a full container/production deployment.

Two further research requests test cancellation with work deliberately held before
market validation and after actual Python validation. After native stop acknowledgement,
the application must remain nonterminal, keep following research queued and have
no tool exit receipt until the original tool returns. Unvalidated cancelled data
must not become a dataset; validated late data is saved with a partial result and
cancelled receipt. The replacement Worker then shuts down cleanly on SIGTERM.
The test uses a random schema and removes it on exit; all owned subprocesses are
cleaned up. No production registry is read and no passing deployment report is
written. These tests must
be invoked explicitly; missing prerequisites fail rather than silently skipping.
The pinned native planner treats these plugin tools as sequential barriers;
the two-tool completion does not certify concurrent dispatch inside one Profile.

These tests do not prove a complete Hermes process works with a real provider.
Keep `toolIdentityInjection` and the deployment evidence gate closed until the
running custom image passes full restart, parallel-tool and cancellation checks.

## On-demand lifecycle supervisor

`pnpm hermes:supervise VERIFIED_REGISTRY PREPARED_DIRECTORY...` runs a separate
host process. Load the Worker database credentials, the registry's API-key
variables and `TOOL_CAPABILITY_KEY` into that process. Apply migrations through 016 and
their explicit grants first. The verified registry gate is unchanged: this command
will not start a Profile lacking a passing deployment report. Start the
supervisor before enabling Worker traffic for these Profiles. Preparation of a
new user's credentials/configuration remains an explicit deployment step.

The supervisor enrolls only the supplied prepared directories. It validates
owner/role/Profile, the local custom image ID (`docker image inspect --format
'{{.Id}}' alpha-lab-hermes:local`), exact generated launch configuration, matching
credentials, private home identity, resource limits and independent mounts and
networks. A deployment hash and home filesystem identity detect changes across
supervisor restarts. It checks the actual container before control operations;
a different container ID is never silently adopted after enrollment.

Queued or active tasks request startup. Worker cannot set the durable submission
marker until a managed Profile is ready. With no unfinished task for that role,
the supervisor stops the container after five idle minutes and retains its home,
container and session identities. The owner-locked stopping transition prevents
a new task from submitting while the idle stop is in flight. Database session
locks serialize multiple supervisors for the same Profile.

Generated Compose now uses `restart: "no"`. Existing `unless-stopped` files are
rejected by the supervisor and need an explicit deployment update and renewed
verification; the application does not overwrite existing preparation directories.
No automatic pull/build, forced recreation, container removal, volume deletion or
orphan cleanup is performed. A container that exited with a submitted unfinished
task stays unknown. Uncertain control results, a replaced container or an
unfinished stop left by a supervisor crash also remain unavailable until operator
reconciliation. The stopped-container recovery command below covers control
uncertainty; it does not settle a lost run or tool execution.

Only this trusted host process needs Docker access. It must also be able to read
the private home marker after the upstream entrypoint changes ownership to UID
10000 (typically a controlled privileged service on Linux). Keep the home private;
do not give the Web, tool service or Hermes a Docker socket. The supervisor can
use the existing restricted Worker DB account with the explicit lifecycle column
grants; it does not need the database migration/owner account.

Current evidence includes PostgreSQL/Worker scheduling and concurrency, driver
contract tests, and the real Docker Desktop container test below. The production
DockerProfileDriver inspects all six containers; ProfileSupervisor stops an idle
research Profile and starts that same container after real TaskStore admission,
then records readiness and the new startup observation. Production Linux home
ownership/networking and provider-backed deployment remain separate gates.

### Real container acceptance with synthetic peers

```sh
HERMES_IMAGE=sha256:<built-custom-image-ID> \
TEST_DATABASE_URL=postgresql://.../alpha_test \
uv run pytest -q tests/hermes/container_integration.py
```

This opt-in test needs Docker Desktop (`host.docker.internal` reaches its local
fixture peer), Node dependencies and a disposable PostgreSQL test database. It
creates two users using real registration/preparation, six independent homes,
keys, networks and containers, and cleans up only those random test projects and
their database schema. It uses the actual upstream entrypoint, UID 10000 gateway,
private mounts, resource limits and application Docker driver/controller.

Each role completes a native run using its own private memory and tool capability;
foreign API keys are rejected, other memories and runtime credentials are absent
from model requests. Restart/replay preserves the original run without another
tool invocation. SIGKILL of the actual gateway exits the container without an
automatic restart; booting with the resulting persisted native running state
still creates no independently supervised gateway slot. An accelerated one-second
idle threshold tests the real controller's stop/demand-start transitions without
changing its production five-minute default.

A second crash happens during a real Worker-admitted model request, before tools
start. The normal supervisor leaves the stopped Profile unknown and does not
restart it. Recovery refuses the unavailable API. The test then explicitly
restores that same container as an operator would, checks the original native
unknown run, and invokes RuntimeRecovery through the production Docker driver.
It confirms stop/start, native cancellation, six audit steps, Worker archival as
interrupted, credential retirement and recorded event loss; recovery itself does
not write a task result. No model/tool execution is repeated. Restoring the API
is an explicit fixture/operator prerequisite, not a new automatic recovery path.

The model and tool peer responses are synthetic. This test constructs in-memory
test bindings from the prepared files; it does not invoke the provider-evidence
registry loader or write a passing registry/report. It does not certify real
provider authorization, Linux mount ownership, automatic recovery of an
unavailable API, or unknown external-tool exit evidence.

### Linux home ownership acceptance

```sh
HERMES_IMAGE=sha256:<built-custom-image-ID> \
uv run pytest -q tests/hermes/linux_permissions_integration.py
```

This test uses temporary Docker-daemon-local Linux volumes so Docker Desktop's
host bind-mount ownership translation cannot mask UID errors. A private home and
mode-0600 marker start at UID/GID 17001; the actual image initializes them, drops
to UID 10000 and completes a native tool run. The marker remains mode 0600 and
an unrelated file retains its original owner and permissions. Separate cases
reject foreign identity, a symlink and hardlinks owned by either the preparing
UID or the runtime UID. Initialization failure stops the container before any
binding store or model/tool activity. Only each test's own container and volume
are removed. This verifies Linux filesystem permissions inside Docker's Linux
VM; remote Linux host service permissions and networking are still deployment
checks, and the model/tool peer is synthetic.

### Full research through actual containers and tools

```sh
HERMES_IMAGE=sha256:<built-custom-image-ID> \
TEST_DATABASE_URL=postgresql://.../alpha_test \
uv run pytest -q tests/hermes/container_system_integration.py
```

This Docker Desktop scenario uses real registration and preparation to start
three role containers, then runs the same complete acceptance flow as the
process system test: Worker SIGKILL after native acceptance but before response
archival, lease-expiry takeover with the original key, specialist dispatch,
Market/Quant reference persistence, all five Canvas components, cross-user read
denial, and research-container restart before the underspecified one-year
follow-up. Two cancellation cases hold provider/validated-data completion until
after a native stop acknowledgement, proving that Worker waits for the actual
tool receipt and does not release the next queued turn early.

The tools HTTP gateway, PostgreSQL stores, Quant subprocesses, worker service and
Hermes runs are real. Only model decisions and market bars are fixtures. Unlike
the isolation test's simple synthetic tool responder, this scenario runs the
application's actual tool handlers. It requires no locally installed Hermes
source tree. Runtime identities include the tested custom image; the response-
loss proxy and private test bindings stay inside the test harness. No passing
production registry or live-provider evidence is written. Cleanup stops only
the generated owner Compose project, test Worker processes and database schema.


## Inspecting and reconciling a stopped Profile

Use the supervisor's private environment and verified deployment files:

```sh
pnpm hermes:reconcile inspect VERIFIED_REGISTRY PREPARED_DIRECTORY PROFILE_ID
pnpm hermes:reconcile confirm-stopped VERIFIED_REGISTRY PREPARED_DIRECTORY PROFILE_ID EXPECTED_CONTAINER_ID
```

`inspect` is read-only. Its JSON contains the bound deployment hash/container ID,
observed container state, counts of unfinished tasks/submitted executions/unresolved
tools and calls, and the latest ten lifecycle audit rows. It does not print
credentials, home paths, prompts or tool arguments. It is a point-in-time snapshot;
`confirm-stopped` always checks again. Use the original recorded full 64-character
container ID, not the ID of a replacement. A busy Profile returns
`profile_control_busy`; let the existing control operation finish before retrying.

`confirm-stopped` requires the exact enrolled owner/role/Profile/deployment and
original container. The driver rechecks immutable image, mounts, limits and
isolation; the container must be created/exited with no process. Database checks
before and after inspection reject any submitted unfinished execution or unresolved
tool/call, including an unknown create with no run ID. The command shares the
supervisor's per-Profile session lock, but does not hold an owner transaction
while waiting on Docker. The final owner-locked transaction changes only the
Profile to stopped and clears its lifecycle error. It never starts/stops a
container, changes task outcomes, writes exit receipts, or clears unknown runs.
Queued work may arrive during inspection; Worker still cannot submit it. After
successful reconciliation the supervisor may start the same container for demand.
No other host service/operator should concurrently control these containers.

Migration 014 records lifecycle state, error, container/binding changes and the
actual database login in append-only `profile_lifecycle_audit`. A trigger writes
these records in the same transaction as the change; runtime services cannot
forge or edit them. The command returns the resulting audit sequence; repeating
it while still stopped is a no-op. No past observations are invented when
upgrading (a pre-migration no-op can return a null sequence). The audit records
state transitions and the shared service login, not an authenticated human identity
or independent proof of Docker inspection. Polls that only update last-use time
do not add entries. Audit retention is independent of transient SSE cleanup.

A missing/replaced original container, changed prepared files/home, failed Docker
inspection or live/unconfirmed process keeps recovery closed. An unfinished
submitted run or tool remains unknown until external execution evidence is
available; external tool executor settlement remains unfinished. The native-run command below
handles a reachable unknown run with no unresolved tool work. Do not manually
mark it completed or replace/restart its runtime to bypass this check.


## Running gateway crash: verified native boundary

The opt-in runtime test now kills the research gateway process group while its
model request is held, waits for the SIGKILL exit, and starts the same private
home. The pinned canonical authority returns `unknown` for the old run with the
same admission ID and execution generation. Exact-key replay returns the old
run without another model/tool execution. A subsequent same-session run stays
queued. Do not infer that every restarted run becomes `interrupted` from the
fallback idempotency code: the canonical admission projection takes precedence.

In this isolated test only, after the original process is confirmed gone and
before any tool has started, the test calls native
`POST /v1/runs/{run_id}/resolve-unknown` with the exact `admission_id` and
`execution_generation`. Incorrect identity/generation is rejected with HTTP 409.
An exact repeat is idempotent; the old turn becomes terminal (native polling maps
its interrupted outcome to `cancelled`) and only the queued follower executes.
Resolving native FIFO uncertainty does not establish that external tool executors
have exited. The application command below adds tool/child checks, controlled
process exit and durable operation tracking; those checks remain mandatory.

The canonical projection, session authority and runtime store source hashes are
now included in `upstream.json` runtime pins, checked against the original fixed
source archive and by the full-process fixture. The production image and its
actual deployment still require separate acceptance.

Migration 015 stores the observed container start token on managed Profiles and
copies container ID/start/deployment hash into the execution at first submission.
The raw Docker timestamp is kept without losing nanoseconds; later replays never
rewrite the original observation. Startup-token changes enter immutable lifecycle
audit. Old executions are not backfilled. These are observations from supervision,
not a handshake proving which process accepted a run, nor standalone exit evidence;
no automatic unknown resolution is enabled by a changed token. A running managed
container lacking a valid start token is not admitted for new submissions.


## Interrupt a known unknown run

```sh
pnpm hermes:reconcile interrupt-run VERIFIED_REGISTRY PREPARED_DIRECTORY PROFILE_ID TASK_ID
```

This is an explicit operator interruption, never an automatic Worker action. It
requires a managed Profile and execution with the original container/deployment
observations, unchanged runtime identity, a known native run ID, and a reachable
runtime reporting canonical `unknown`. The command refuses unresolved tool
operations, unfinished side-effecting calls, active children or another submitted
execution for that Profile. It cannot settle lost external tool executors, repair
missing run identity, adopt replacement containers, or bootstrap an unreachable
runtime. `inspect` also returns recent recovery records and their current phase.

After validating the exact native run/session/admission/generation, it atomically
records a `stopping` recovery and closes the Profile submission gate. It stops
only the bound original container and confirms stopped state before restarting
that same container. It verifies a new start observation and health, checks the
original native admission again, and invokes `resolve-unknown` only for that
admission/generation. Admission IDs are opaque strings, not application UUIDs;
the pinned runtime uses UUID hex without hyphens, preserved exactly throughout.
Only a subsequent matching terminal status read permits the recovery to finish.

The six durable phases are `stopping`, `stopped`, `starting`, `started`, `resolving`
and `resolved`. Before every external action its intent is committed; no owner
transaction is held over Docker/HTTP waits. A shared per-Profile advisory lock
excludes the supervisor and other operators. Each phase appends an immutable
`recovery.*` task audit event with the recovery ID and actual database login;
identity and completed observations cannot be overwritten or phases rewound.
The BFF exposes the recovery ID through owner-scoped task audit, not the database
login. The login identifies a service, not an authenticated human operator.

If a stop/start/resolve response is lost, the command exits with the gate still
closed. Re-run the same command: it re-inspects before proceeding and resumes the
stored phase. A confirmed stopped container does not receive another stop; an
already started matching incarnation does not receive another start. A terminal
native read after a lost resolution response avoids resending the resolution.
An exact resolved retry returns the recorded result without controlling anything.
`confirm-stopped` cannot bypass a pending run recovery. Unexpected container or
native identity changes leave the Profile unavailable for further review.

The operator does not write task outputs or pretend tool exit receipts exist.
Worker independently reads the native terminal output and performs its existing
settlement rules. A discarded unknown turn becomes `interrupted` unless an actual
user cancellation applies; recovered native terminal evidence retains its normal
mapping. The final native resolution may advance a native FIFO, so these private
Profiles must have no other direct submission clients. Application submissions
remain behind the database gate throughout recovery.

Verified evidence includes real PostgreSQL state/concurrency and restricted-role
checks, lost acknowledgement cases, and a real pinned Hermes process test that
kills the initial gateway, restarts to observe unknown, then drives this application
recovery with actual process-group stop/start and native HTTP calls. It confirms
one model execution, no repeated tool call, six audit steps, Worker interruption
archival, token retirement and recorded event loss. Process control in this test
is a local test driver, not the production Docker driver. The separate container
test below also covers isolation, lifecycle and unknown-run recovery during an
active container model call. No passing registry report is produced.


### Follow-up context after a gateway restart

The real three-role system test also completes an initial NVDA research, stops
and restarts the research gateway using its original private home, then submits
only `把它改成一年` through the normal Worker. The controlled model must find the
previous confirmed symbol, end date and final answer in the native conversation;
the application does not resend the first question in the follow-up request.
The same Profile/session is retained while the new task/run/key/capability remain
independent. Real Market/Quant handlers persist the new one-year dataset and two
Canvas components, retain the earlier five-component revision, and reject a
second user's access to the new dataset. The expanded market fixture contains
synthetic XNYS sessions and is used only by this opt-in test. This is transport,
context persistence and data-flow evidence, not proof of a real model's reasoning
or real market data authorization.
