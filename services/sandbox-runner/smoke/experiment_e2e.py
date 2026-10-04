#!/usr/bin/env python3
"""S08 mock acceptance driver: the isolated exploration-loop acceptance.

Runs on the SG HOST (needs the repo venv: youwei_contracts + httpx +
cryptography) against a THROWAWAY stack it brings up itself:

    youwei-experiment (internal net)  <- experiment containers + the Runner
    youwei-research   (internal net)  <- research containers + mock gateway
    mock-gateway      (both nets)     <- scripted SSE model (zero cost)
    runner container  (docker socket, published on 127.0.0.1)

Phases (each prints a verdict; exit 0 iff all pass):

  A. network probes
     - experiment container: {gateway, runner tool endpoint} reachable;
       external egress / docker socket / host / research network blocked.
     - research container: the RUNNER is NOT reachable (D1: research
       containers keep their gateway-only egress).

  B. HTTP over-privilege matrix against the live Runner
     - tool tokens cannot reach the sandbox/research/control/dispatch
       entries; admin grants cannot reach the tool plane or dispatch;
       dispatch grants cannot reach the control plane; research grants
       cannot reach the experiment surface; scope/binding negatives.

  C. end-to-end exploration loop (zero cost, real containers)
     - research turn 1 (real research container + scripted model) answers
       with an ExperimentRequest;
     - the driver (standing in for the Controller's Runner-side surface)
       registers the authorization, dispatches the experiment instance
       (real experiment container whose tools make REAL tool-plane calls);
     - the generated code runs in a REAL sandbox container against the
       Runner-injected snapshot and produces a verified artifact;
     - receipts are fetched and cross-checked against the instance result
       (the same verification accept_experiment performs);
     - research re-entry turn cites the artifact (kind "code") and returns
       a proposal; the citation resolves against the receipts.

No database is involved: the Core-side registration/acceptance (fencing,
caps, append-only) is covered by the SG DB tests; this acceptance pins the
Runner + container + network + token surface.

Usage (SG host, repo venv):
  python services/sandbox-runner/smoke/experiment_e2e.py \
      --repo /root/youwei-trading-agent --image youwei/agent-runtime:s08c \
      [--keep] [--phase all|probe|matrix|loop]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from youwei_contracts.agent_runtime import (
    ResearchInvocationEnvelope,
    ResearchInvocationRequest,
    ResearchRuntimeConfig,
)
from youwei_contracts.experiment import (
    ExperimentAuthorization,
    ExperimentComputationReceipt,
    ExperimentLimits,
    ExperimentRequest,
    ExperimentResult,
    ExperimentResultComputation,
    ExperimentInvocationEnvelope,
    ExperimentInvocationRequest,
    ExperimentRuntimeConfig,
    experiment_invocation_digest,
)
from youwei_contracts.research import FrozenEvidence
from youwei_contracts.research_capability import (
    AUD_RUNNER_EXEC,
    AUD_RUNNER_TOOLS,
    AUD_RUNTIME_EXPERIMENT,
    AUD_RUNTIME_RESEARCH,
    SCOPE_EXPERIMENT_ADMIN,
    SCOPE_EXPERIMENT_READ,
    SCOPE_EXPERIMENT_RUN,
    SCOPE_EXPERIMENT_RUN_STATUS,
    SCOPE_EXPERIMENT_STATUS,
    SCOPE_EXPERIMENT_SUBMIT,
    SCOPE_RESEARCH_RUN,
    generate_research_keypair,
    public_key_thumbprint,
    sign_research_token,
)
from youwei_contracts.sandbox import SnapshotBundle

EXEC_CONFIG = "s08-accept-v1"
GATEWAY_PORT = 9901
RUNNER_PORT = 8091
GATEWAY_ALIAS = "mock-gateway"
RUNNER_ALIAS = "s08-runner"


def sh(*cmd, check=True, capture=True) -> str:
    result = subprocess.run(
        list(cmd), capture_output=capture, text=True, check=False
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}"
        )
    return (result.stdout or "") + (result.stderr or "")


def sh_ok(*cmd) -> bool:
    return subprocess.run(
        list(cmd), capture_output=True, text=True, check=False
    ).returncode == 0


class Verdict:
    def __init__(self):
        self.failures = 0

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""), flush=True)
        if not ok:
            self.failures += 1


class Stack:
    """The throwaway acceptance stack (networks, mock gateway, Runner)."""

    def __init__(self, repo: Path, image: str):
        self.repo = repo
        self.image = image
        self.priv, self.pub = generate_research_keypair()
        self.kid = public_key_thumbprint(self.pub)
        self.created_networks: list[str] = []
        self.runner_container = "s08-accept-runner"
        self.gateway_container = "s08-accept-gateway"
        self.store_dir = Path("/tmp/s08-accept-store")

    def up(self):
        smoke = self.repo / "services/sandbox-runner/smoke"
        # defensive: clear leftovers from an aborted earlier run
        sh("docker", "rm", "-f", self.runner_container, check=False)
        sh("docker", "rm", "-f", self.gateway_container, check=False)
        for net in ("youwei-research", "youwei-experiment"):
            if not sh_ok("docker", "network", "inspect", net):
                sh("docker", "network", "create", "--internal", net)
                self.created_networks.append(net)
        # mock gateway on BOTH networks (alias mock-gateway)
        sh(
            "docker", "run", "-d", "--rm", "--name", self.gateway_container,
            "--network", "youwei-research", "--network-alias", GATEWAY_ALIAS,
            "-v", f"{smoke}:/smoke:ro",
            "--entrypoint", "python3", self.image,
            "/smoke/experiment_mock_gateway.py", str(GATEWAY_PORT), "0.0.0.0",
        )
        sh(
            "docker", "network", "connect",
            "--alias", GATEWAY_ALIAS, "youwei-experiment", self.gateway_container,
        )
        # the Runner: docker socket + experiment store. NO published port:
        # docker-proxy on --internal networks is unreliable, and the host can
        # reach the internal bridge directly — the driver resolves the
        # container IP and talks to it straight.
        # The SPOOL must be a SAME-PATH bind mount: the Runner materializes
        # snapshot/script files inside its own filesystem and then passes
        # those paths to `docker run -v`, which the HOST daemon resolves —
        # the host path and the container path must be IDENTICAL (the
        # dev-compose pattern; a differing pair silently mounts an empty
        # auto-created host dir).
        self.store_dir.mkdir(parents=True, exist_ok=True)
        spool_dir = Path("/tmp/youwei-runner")
        spool_dir.mkdir(parents=True, exist_ok=True)
        public_keys = json.dumps({self.kid: self.pub})
        sh(
            "docker", "run", "-d", "--rm", "--name", self.runner_container,
            "--network", "youwei-experiment", "--network-alias", RUNNER_ALIAS,
            "-v", "/var/run/docker.sock:/var/run/docker.sock",
            "-v", f"{self.store_dir}:/store",
            "-v", f"{spool_dir}:/tmp/youwei-runner",
            "-e", "YOUWEI_RUNNER_SECRET=s08-acceptance-secret-32-bytes!!",
            "-e", "YOUWEI_RUNNER_DEVELOPMENT=true",
            "-e", "YOUWEI_RUNNER_RUNTIME=",
            "-e", "YOUWEI_RUNNER_IMAGE=python:3.13-alpine",
            "-e", f"YOUWEI_RUNNER_AGENT_RUNTIME_IMAGE={self.image}",
            "-e", f"YOUWEI_RUNNER_AGENT_RUNTIME_GATEWAY_URL=http://{GATEWAY_ALIAS}:{GATEWAY_PORT}",
            "-e", f"YOUWEI_RUNNER_AGENT_RUNTIME_GATEWAY_HOST={GATEWAY_ALIAS}:{GATEWAY_PORT}",
            "-e", f"YOUWEI_RUNNER_AGENT_RUNTIME_PUBLIC_KEYS={public_keys}",
            "-e", f"YOUWEI_RUNNER_AGENT_RUNTIME_EXEC_CONFIG_VERSION={EXEC_CONFIG}",
            "-e", "YOUWEI_RUNNER_EXPERIMENT_STORE_DIR=/store",
            "-e", f"YOUWEI_RUNNER_EXPERIMENT_TOOL_BASE_URL=http://{RUNNER_ALIAS}:8091",
            "-e", "YOUWEI_GATEWAY_API_KEY=s08-mock-gateway-key",
            "youwei-runner:development",
        )
        # wait for the Runner healthz
        import time

        ip = sh(
            "docker", "inspect", "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            self.runner_container,
        ).strip()
        self.base_url = f"http://{ip}:8091"
        for _ in range(60):
            try:
                response = httpx.get(f"{self.base_url}/healthz", timeout=2)
                if response.status_code == 200:
                    print("runner up at", self.base_url, response.json(), flush=True)
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        logs = sh(
            "docker", "logs", self.runner_container, check=False
        )
        raise RuntimeError(
            f"runner did not come up; container logs:\n{logs[-3000:]}"
        )

    def down(self):
        sh("docker", "rm", "-f", self.runner_container, check=False)
        sh("docker", "rm", "-f", self.gateway_container, check=False)
        for net in self.created_networks:
            sh("docker", "network", "rm", net, check=False)
        # clear stray auto-created spool dirs from failed runs
        sh("rm", "-rf", "/tmp/youwei-runner", check=False)

    # --- token minting ---------------------------------------------------

    def token(self, *, aud, scopes, invocation_id, tenant, run_, job,
              attempt_no, case, evidence_sha, exp_minutes=5, **overrides):
        binding = dict(
            invocation_id=invocation_id,
            tenant_id=tenant, run_id=run_, job_id=job, attempt_no=attempt_no,
            case_id=case, evidence_sha256=evidence_sha,
            exec_config_version=EXEC_CONFIG,
        )
        binding.update(overrides)
        sign_kwargs = {
            "kid": self.kid, "aud": aud, "scopes": scopes,
            "exp": datetime.now(UTC) + timedelta(minutes=exp_minutes),
        }
        # invocation_id may be overridden — never pass it twice
        sign_kwargs.update(binding)
        return sign_research_token(self.priv, **sign_kwargs)


# --- phase A: network probes ---------------------------------------------------


def phase_probe(stack: Stack, verdict: Verdict, smoke: Path):
    print("\n== A. network probes ==", flush=True)
    out = sh(
        "docker", "run", "--rm", "--network", "youwei-experiment",
        "-v", f"{smoke}:/smoke:ro",
        "-e", f"RUNNER_ADDR={RUNNER_ALIAS}:8091",
        "-e", f"GATEWAY_ADDR={GATEWAY_ALIAS}:{GATEWAY_PORT}",
        "-e", f"RESEARCH_NET_ADDR={GATEWAY_ALIAS}.youwei-research:{GATEWAY_PORT}",
        "--entrypoint", "python3", stack.image,
        "/smoke/experiment_net_probe.py", check=False,
    )
    print(out.strip(), flush=True)
    verdict.check("experiment network probe", "NET_PROBE OK" in out, out.strip()[-120:])

    # research-side probe: the Runner must NOT be resolvable/reachable from
    # the research network (it is only attached to youwei-experiment).
    out = sh(
        "docker", "run", "--rm", "--network", "youwei-research",
        "-v", f"{smoke}:/smoke:ro",
        "-e", f"RUNNER_ADDR={RUNNER_ALIAS}:8091",
        "-e", f"GATEWAY_ADDR={GATEWAY_ALIAS}:{GATEWAY_PORT}",
        "--entrypoint", "python3", stack.image,
        "/smoke/experiment_net_probe.py", check=False,
    )
    print(out.strip(), flush=True)
    checks = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                row = json.loads(line)
                checks[row.get("check")] = row.get("ok")
            except ValueError:
                continue
    runner_blocked = checks.get("runner_tool_endpoint") is False
    gateway_ok = checks.get("gateway_reachable") is True
    verdict.check(
        "research network: gateway reachable, runner blocked",
        gateway_ok and runner_blocked,
        "runner blocked" if runner_blocked else "runner reachable (must be blocked!)",
    )


# --- phase B: over-privilege matrix ---------------------------------------------


def phase_matrix(stack: Stack, verdict: Verdict, base):
    print("\n== B. HTTP over-privilege matrix ==", flush=True)
    client = httpx.Client(base_url=stack.base_url, timeout=15)
    ids = base

    def token(aud, scopes, **overrides):
        invocation = overrides.pop("invocation_id", ids["experiment"])
        return stack.token(
            aud=aud, scopes=scopes,
            invocation_id=invocation,
            tenant=ids["tenant"],
            run_=ids["run"], job=ids["job"], attempt_no=1,
            case=ids["case"], evidence_sha=ids["evidence_sha"], **overrides,
        )

    tool = token(AUD_RUNNER_TOOLS, (SCOPE_EXPERIMENT_SUBMIT,))
    admin = token(AUD_RUNNER_EXEC, (SCOPE_EXPERIMENT_ADMIN,))
    dispatch = token(AUD_RUNNER_EXEC, (SCOPE_EXPERIMENT_RUN,))
    research = token(AUD_RUNNER_EXEC, (SCOPE_RESEARCH_RUN,))

    def expect(name, method, path, token_value, *, want, body=None):
        response = client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {token_value}"}
        )
        ok = response.status_code == want
        verdict.check(
            name, ok,
            f"{method} {path} -> {response.status_code} (want {want})",
        )

    # tool tokens cannot reach any control or dispatch surface
    expect("tool x sandbox executions", "POST", "/v1/executions", tool, want=403)
    expect("tool x research invocations", "POST", "/v1/research-invocations", tool, want=403)
    expect("tool x experiment authorizations", "POST", "/v1/experiment-authorizations", tool, want=403)
    expect("tool x experiment dispatch", "POST", "/v1/experiment-invocations", tool, want=403)
    # ...but the tool plane itself authorizes it (404 = passed the gate)
    expect(
        "tool x tool plane (auth ok, unknown computation)", "GET",
        f"/v1/experiment-computations/{ids['experiment']}/{uuid.uuid4()}",
        token(AUD_RUNNER_TOOLS, (SCOPE_EXPERIMENT_STATUS,)), want=404,
    )
    # admin grants cannot dispatch instances or reach the tool plane
    expect("admin x dispatch", "POST", "/v1/experiment-invocations", admin, want=403)
    expect("admin x tool plane", "POST", "/v1/experiment-computations", admin, want=403)
    # dispatch grants cannot reach the control plane or the sandbox
    expect("dispatch x authorizations", "POST", "/v1/experiment-authorizations", dispatch, want=403)
    expect("dispatch x terminate", "DELETE",
           f"/v1/experiment-authorizations/{ids['experiment']}", dispatch, want=403)
    expect("dispatch x receipts", "GET",
           f"/v1/experiment-authorizations/{ids['experiment']}/receipts", dispatch, want=403)
    expect("dispatch x sandbox executions", "POST", "/v1/executions", dispatch, want=403)
    # research grants cannot reach the experiment surface
    expect("research x authorizations", "POST", "/v1/experiment-authorizations", research, want=403)
    expect("research x dispatch", "POST", "/v1/experiment-invocations", research, want=403)
    expect("research x tool plane", "POST", "/v1/experiment-computations", research, want=403)
    # scope negatives
    expect("run_status alone cannot dispatch", "POST", "/v1/experiment-invocations",
           token(AUD_RUNNER_EXEC, (SCOPE_EXPERIMENT_RUN_STATUS,)), want=403)
    expect("submit scope cannot poll computations", "GET",
           f"/v1/experiment-computations/{ids['experiment']}/{uuid.uuid4()}",
           token(AUD_RUNNER_TOOLS, (SCOPE_EXPERIMENT_SUBMIT,)), want=403)
    # binding negatives: a token for ANOTHER experiment
    expect("cross-experiment binding", "GET",
           f"/v1/experiment-computations/{ids['experiment']}/{uuid.uuid4()}",
           token(AUD_RUNNER_TOOLS, (SCOPE_EXPERIMENT_STATUS,),
                 invocation_id=uuid.uuid4()), want=403)
    # bad signature
    other_priv, _ = generate_research_keypair()
    forged = sign_research_token(
        other_priv, kid=stack.kid, aud=AUD_RUNNER_TOOLS,
        scopes=(SCOPE_EXPERIMENT_SUBMIT,),
        invocation_id=ids["experiment"], tenant_id=ids["tenant"],
        run_id=ids["run"], job_id=ids["job"], attempt_no=1, case_id=ids["case"],
        evidence_sha256=ids["evidence_sha"], exec_config_version=EXEC_CONFIG,
        exp=datetime.now(UTC) + timedelta(minutes=5),
    )
    expect("forged signature", "POST", "/v1/experiment-computations", forged, want=403)
    client.close()


# --- phase C: the exploration loop ----------------------------------------------


def _frozen_evidence(base):
    evidence, canonical = _frozen_evidence_parts(base)
    return evidence


def _frozen_evidence_parts(base):
    """(FrozenEvidence, canonical content string) — the SnapshotBundle needs
    the exact canonical JSON the content hash covers."""
    security = str(base["security"])
    benchmark = str(base["benchmark"])
    rows = [
        {"security_id": security, "trade_date": f"2026-09-{20 + i:02d}",
         "close": 100.0 + i * 0.5}
        for i in range(6)
    ] + [
        {"security_id": benchmark, "trade_date": f"2026-09-{20 + i:02d}",
         "close": 100.0 + i * 0.25}
        for i in range(6)
    ]
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    sha = hashlib.sha256(canonical.encode()).hexdigest()
    evidence = FrozenEvidence(
        run_id=base["run"], tenant_id=base["tenant"],
        case={
            "case_id": str(base["case"]), "security_id": str(base["security"]),
            "benchmark_security_id": str(base["benchmark"]), "horizon_td": 20,
            "target_spec_id": "excess-tr-d20-v1", "target_spec_sha256": "a" * 64,
            "decision_cutoff_utc": "2026-10-10T10:00:00+00:00",
            "prediction_deadline_utc": "2026-10-12T13:15:00+00:00",
            "entry_at_utc": "2026-10-12T13:30:00+00:00",
            "exit_at_utc": "2026-11-09T20:00:00+00:00",
        },
        evidence={
            "snapshot_id": str(base["snapshot"]), "kind": "daily_bars",
            "as_of": "2026-10-10T10:00:00+00:00", "mode": "forward",
            "content_sha256": sha, "content": rows,
            "manifest": {
                "code_version": "daily-bars-snapshot-v1",
                "query": {"mode": "forward", "as_of": "2026-10-10T10:00:00+00:00"},
                "content_sha256": sha, "row_count": len(rows),
                "coverage": {security: 6, benchmark: 6},
            },
        },
        target_policy_sha256="b" * 64,
        batch_manifest={"calendar_version": "nyse-rules-v1"},
        quant={
            "model_version": "quant-momentum-v0",
            "source_status": "produced",
            "p_outperform": 0.55,
            "expected_excess_return": 0.01,
        },
    )
    return evidence, canonical


def _evidence_sha(evidence: FrozenEvidence) -> str:
    canonical = json.dumps(
        evidence.model_dump(mode="json"),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


async def _poll(client, path, token, *, until, timeout=300):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get(
            path, headers={"Authorization": f"Bearer {token}"}
        )
        view = response.json()
        if view.get("status") != "running":
            return view
        await asyncio.sleep(0.5)
    raise RuntimeError(f"poll timeout: {path}")


async def phase_loop(stack: Stack, verdict: Verdict):
    print("\n== C. end-to-end exploration loop ==", flush=True)
    base = {
        "tenant": uuid.uuid4(), "run": uuid.uuid4(), "job": uuid.uuid4(),
        "case": uuid.uuid4(), "security": uuid.uuid4(),
        "benchmark": uuid.uuid4(), "snapshot": uuid.uuid4(),
        "experiment": uuid.uuid4(), "evidence_sha": None,
    }
    evidence, canonical = _frozen_evidence_parts(base)
    base["evidence_sha"] = _evidence_sha(evidence)

    def token(aud, scopes, invocation_id, exp_minutes=5, **overrides):
        return stack.token(
            aud=aud, scopes=scopes, invocation_id=invocation_id,
            tenant=base["tenant"], run_=base["run"], job=base["job"],
            attempt_no=1, case=base["case"], evidence_sha=base["evidence_sha"],
            exp_minutes=exp_minutes, **overrides,
        )

    async with httpx.AsyncClient(base_url=stack.base_url, timeout=60) as client:
        # 1) research turn 1 -> experiment request
        invocation_1 = uuid.uuid4()
        request_1 = ResearchInvocationRequest(
            invocation_id=invocation_1,
            tenant_id=base["tenant"], run_id=base["run"], job_id=base["job"],
            attempt_no=1, case_id=base["case"],
            evidence=evidence, evidence_sha256=base["evidence_sha"],
            exec_config_version=EXEC_CONFIG,
            config=ResearchRuntimeConfig(model="mock"),
        )
        envelope = ResearchInvocationEnvelope(
            request=request_1,
            runtime_token=token(
                AUD_RUNTIME_RESEARCH, (SCOPE_RESEARCH_RUN,), invocation_1
            ),
        )
        response = await client.post(
            "/v1/research-invocations",
            json=envelope.model_dump(mode="json"),
            headers={
                "Authorization": "Bearer " + token(
                    AUD_RUNNER_EXEC, (SCOPE_RESEARCH_RUN,), invocation_1
                )
            },
        )
        assert response.status_code == 202, response.text
        view = await _poll(
            client, f"/v1/research-invocations/{invocation_1}",
            token(AUD_RUNNER_EXEC, ("research:status",), invocation_1),
            until="succeeded",
        )
        assert view["status"] == "succeeded", view
        result_1 = view["result"]
        ask = result_1.get("experiment_request")
        verdict.check(
            "research turn 1 answers with an experiment request",
            bool(ask) and not result_1.get("proposal"),
            (ask or {}).get("question", "")[:60],
        )
        question = ExperimentRequest.model_validate(ask)

        # 2) register the authorization (control plane)
        snapshot_bundle = SnapshotBundle(
            snapshot_id=base["snapshot"],
            content=canonical,
            manifest=evidence.evidence.manifest,
        )
        auth = ExperimentAuthorization(
            experiment_invocation_id=base["experiment"],
            tenant_id=base["tenant"], run_id=base["run"], job_id=base["job"],
            attempt_id=uuid.uuid4(), attempt_no=1, case_id=base["case"],
            evidence_sha256=base["evidence_sha"],
            exec_config_version=EXEC_CONFIG,
            limits=ExperimentLimits(
                max_computations=2, max_concurrent=1,
                max_total_duration_seconds=300.0,
                max_artifact_bytes=1024 * 1024,
            ),
            snapshot=snapshot_bundle,
        )
        response = await client.post(
            "/v1/experiment-authorizations",
            json=auth.model_dump(mode="json"),
            headers={
                "Authorization": "Bearer " + token(
                    AUD_RUNNER_EXEC, (SCOPE_EXPERIMENT_ADMIN,), base["experiment"]
                )
            },
        )
        verdict.check(
            "authorization registered", response.status_code == 200, response.text[:80]
        )

        # 3) dispatch the experiment instance
        invocation_req = ExperimentInvocationRequest(
            experiment_invocation_id=base["experiment"],
            tenant_id=base["tenant"], run_id=base["run"], job_id=base["job"],
            attempt_no=1, case_id=base["case"],
            evidence_sha256=base["evidence_sha"],
            exec_config_version=EXEC_CONFIG,
            question=question,
            snapshot_manifest=evidence.evidence.manifest,
            config=ExperimentRuntimeConfig(model="mock"),
        )
        exp_env = ExperimentInvocationEnvelope(
            request=invocation_req,
            runtime_token=token(
                AUD_RUNTIME_EXPERIMENT, (SCOPE_EXPERIMENT_RUN,), base["experiment"]
            ),
            tool_token=token(
                AUD_RUNNER_TOOLS,
                (SCOPE_EXPERIMENT_SUBMIT, SCOPE_EXPERIMENT_STATUS, SCOPE_EXPERIMENT_READ),
                base["experiment"], exp_minutes=30,
            ),
        )
        response = await client.post(
            "/v1/experiment-invocations",
            json=exp_env.model_dump(mode="json"),
            headers={
                "Authorization": "Bearer " + token(
                    AUD_RUNNER_EXEC, (SCOPE_EXPERIMENT_RUN,), base["experiment"]
                )
            },
        )
        assert response.status_code == 202, response.text
        view = await _poll(
            client, f"/v1/experiment-invocations/{base['experiment']}",
            token(AUD_RUNNER_EXEC, (SCOPE_EXPERIMENT_RUN_STATUS,), base["experiment"]),
            until="succeeded", timeout=420,
        )
        verdict.check(
            "experiment instance dispatch", view["status"] == "succeeded",
            str(view.get("error", ""))[:120],
        )
        assert view["status"] == "succeeded", view
        instance_result = ExperimentResult.model_validate(view["result"]["result"])
        print("instance findings:", instance_result.findings[:300], flush=True)
        print("instance computations:", [
            (str(c.computation_id), c.status) for c in instance_result.computations
        ], flush=True)
        print("instance usage:", view["result"].get("usage", {}).get("source"),
              "complete=", view["result"].get("usage", {}).get("complete"), flush=True)

        # 4) receipts + the acceptance cross-check (pure, no DB)
        response = await client.get(
            f"/v1/experiment-authorizations/{base['experiment']}/receipts",
            headers={
                "Authorization": "Bearer " + token(
                    AUD_RUNNER_EXEC, (SCOPE_EXPERIMENT_ADMIN,), base["experiment"]
                )
            },
        )
        receipts = [
            ExperimentComputationReceipt.model_validate(r) for r in response.json()
        ]
        from youwei_core.ledger.experiment_records import verify_experiment_evidence

        image_used = verify_experiment_evidence(
            experiment_invocation_id=base["experiment"],
            result=instance_result,
            receipts=receipts,
            snapshot_content_sha256=snapshot_bundle.manifest["content_sha256"],
        )
        verdict.check(
            "receipts verify against the instance result",
            bool(instance_result.computations) and bool(image_used),
            f"{len(instance_result.computations)} computation(s), image={image_used[:30]}",
        )
        computation = instance_result.computations[0]
        verdict.check(
            "computation succeeded with a collected artifact",
            computation.status == "succeeded" and bool(computation.artifacts),
            str(computation.artifacts[:1]),
        )

        # 5) research re-entry: cite the artifact, return a proposal
        from youwei_contracts.experiment import (
            ExperimentContext,
            experiment_context_from_result,
        )

        context = experiment_context_from_result(
            base["experiment"], question.question, instance_result
        )
        invocation_2 = uuid.uuid4()
        request_2 = ResearchInvocationRequest(
            invocation_id=invocation_2,
            tenant_id=base["tenant"], run_id=base["run"], job_id=base["job"],
            attempt_no=1, case_id=base["case"],
            evidence=evidence, evidence_sha256=base["evidence_sha"],
            exec_config_version=EXEC_CONFIG,
            config=ResearchRuntimeConfig(model="mock"),
            experiments=[context],
        )
        envelope_2 = ResearchInvocationEnvelope(
            request=request_2,
            runtime_token=token(
                AUD_RUNTIME_RESEARCH, (SCOPE_RESEARCH_RUN,), invocation_2
            ),
        )
        response = await client.post(
            "/v1/research-invocations",
            json=envelope_2.model_dump(mode="json"),
            headers={
                "Authorization": "Bearer " + token(
                    AUD_RUNNER_EXEC, (SCOPE_RESEARCH_RUN,), invocation_2
                )
            },
        )
        assert response.status_code == 202, response.text
        view = await _poll(
            client, f"/v1/research-invocations/{invocation_2}",
            token(AUD_RUNNER_EXEC, ("research:status",), invocation_2),
            until="succeeded",
        )
        assert view["status"] == "succeeded", view
        proposal = view["result"]["proposal"]
        citation = next(
            (r for r in proposal.get("references", []) if r.get("kind") == "code"),
            None,
        )
        verdict.check(
            "re-entry proposal cites the experiment artifact",
            bool(citation) and proposal.get("source_status") == "produced",
            (citation or {}).get("locator", "")[:90],
        )
        if citation:
            from youwei_contracts.experiment import parse_experiment_locator

            exp_id, comp_id, path = parse_experiment_locator(citation["locator"])
            receipt = next(
                (r for r in receipts if r.computation_id == comp_id), None
            )
            artifact_ok = (
                exp_id == base["experiment"]
                and receipt is not None
                and any(a.path == path for a in receipt.artifacts)
            )
            verdict.check("citation resolves to a receipt artifact", artifact_ok)
    print("EXPERIMENT_E2E OK" if verdict.failures == 0 else "EXPERIMENT_E2E FAILED",
          flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[3]))
    parser.add_argument("--image", default="youwei/agent-runtime:s08c")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--phase", default="all",
                        choices=["all", "probe", "matrix", "loop"])
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    smoke = repo / "services/sandbox-runner/smoke"
    verdict = Verdict()
    stack = Stack(repo, args.image)
    stack.up()
    try:
        base = {
            "tenant": uuid.uuid4(), "run": uuid.uuid4(), "job": uuid.uuid4(),
            "case": uuid.uuid4(), "experiment": uuid.uuid4(),
            "evidence_sha": "e" * 64,
        }
        if args.phase in ("all", "probe"):
            phase_probe(stack, verdict, smoke)
        if args.phase in ("all", "matrix"):
            phase_matrix(stack, verdict, base)
        if args.phase in ("all", "loop"):
            asyncio.run(phase_loop(stack, verdict))
    finally:
        if not args.keep:
            stack.down()
    print(f"\nverdict: {verdict.failures} failure(s)", flush=True)
    return 0 if verdict.failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
