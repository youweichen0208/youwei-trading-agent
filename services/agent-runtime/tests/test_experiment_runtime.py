"""S08c-2 agent-runtime experiment surface (pure logic, no Hermes checkout).

Covers: the deterministic brief (question + manifest, no snapshot content),
the findings parser, the computation journal's result assembly (terminal
entries only, code hashes from the actual submitted code), the tool
authorization re-checks (audience/scope/binding), the HTTP tool plumbing
against a mock Runner, and the wire codec (grant verification + result
encoding).
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from youwei_contracts.experiment import (
    ArtifactManifest,
    ExperimentComputationStatus,
    ExperimentInvocationRequest,
    ExperimentRequest,
    ExperimentResult,
    ExperimentRuntimeConfig,
)
from youwei_contracts.research_capability import (
    AUD_RUNNER_EXEC,
    AUD_RUNNER_TOOLS,
    AUD_RUNTIME_EXPERIMENT,
    SCOPE_EXPERIMENT_RUN,
    SCOPE_EXPERIMENT_STATUS,
    SCOPE_EXPERIMENT_SUBMIT,
    generate_research_keypair,
    public_key_thumbprint,
    sign_research_token,
)
from youwei_agent_runtime.experiment import (
    ExperimentInvocationError,
    build_experiment_brief,
    check_experiment_capability,
    encode_experiment_error,
    encode_experiment_result,
    experiment_isolation_kwargs,
    honor_experiment_request,
    parse_experiment_findings,
    run_experiment,
)
from youwei_agent_runtime.experiment_tools import (
    EXPERIMENT_TOOLSET,
    ComputationJournal,
    ExperimentToolAuthorizationError,
    ExperimentToolContext,
    artifact_read_handler,
    current_experiment_tool_context,
    reset_experiment_tool_context,
    sandbox_status_handler,
    sandbox_submit_handler,
    set_experiment_tool_context,
)

EXP = uuid.uuid4()
TENANT = uuid.uuid4()
RUN = uuid.uuid4()
JOB = uuid.uuid4()
CASE = uuid.uuid4()
EV_SHA = "e" * 64
EXEC_CONFIG = "research-exec-2026-10"
ENDPOINT = "http://runner.test"


def _question() -> ExperimentRequest:
    return ExperimentRequest(
        question="20-session rolling realized vol ratio vs benchmark",
        motivation="quant lib does not cover it",
        requested_shape="{ratio: float, quantile: float}",
    )


def _manifest() -> dict:
    return {"snapshot_id": "s1", "row_count": 120, "coverage": {"S1": 60, "SPY": 60}}


def _keys():
    priv, pub = generate_research_keypair()
    kid = public_key_thumbprint(pub)
    return priv, {kid: pub}, kid


def _grant(priv, kid, *, aud=AUD_RUNTIME_EXPERIMENT, scopes=(SCOPE_EXPERIMENT_RUN,),
           **overrides):
    binding = dict(
        invocation_id=EXP,
        tenant_id=TENANT,
        run_id=RUN,
        job_id=JOB,
        attempt_no=1,
        case_id=CASE,
        evidence_sha256=EV_SHA,
        exec_config_version=EXEC_CONFIG,
    )
    binding.update(overrides)
    return sign_research_token(
        priv, kid=kid, aud=aud, scopes=scopes,
        exp=datetime.now(UTC) + timedelta(minutes=5), **binding,
    )


def _invocation_request() -> ExperimentInvocationRequest:
    return ExperimentInvocationRequest(
        experiment_invocation_id=EXP,
        tenant_id=TENANT,
        run_id=RUN,
        job_id=JOB,
        attempt_no=1,
        case_id=CASE,
        evidence_sha256=EV_SHA,
        exec_config_version=EXEC_CONFIG,
        question=_question(),
        snapshot_manifest=_manifest(),
        config=ExperimentRuntimeConfig(model="glm-5.3"),
    )


def _status(computation_id, *, status="succeeded", artifacts=()) -> ExperimentComputationStatus:
    return ExperimentComputationStatus(
        experiment_invocation_id=EXP,
        computation_id=computation_id,
        status=status,
        artifacts=list(artifacts),
        duration_seconds=1.0,
    )


# --- brief ------------------------------------------------------------------------


class TestBrief:
    def test_brief_contains_question_and_manifest_not_content(self):
        brief = build_experiment_brief(_question(), _manifest())
        assert "20-session rolling realized vol ratio" in brief
        assert "s1" in brief and "120" in brief
        # the manifest's JSON is embedded verbatim
        assert json.dumps(_manifest(), sort_keys=True, ensure_ascii=False, indent=1) in brief
        # the sandbox contract and output format are pinned
        assert "/inputs/snapshot/content.json" in brief
        assert "/outputs/" in brief
        assert '"findings"' in brief

    def test_brief_deterministic(self):
        a = build_experiment_brief(_question(), _manifest())
        b = build_experiment_brief(_question(), _manifest())
        assert a == b


# --- findings parsing ---------------------------------------------------------------


class TestFindings:
    def test_plain_json(self):
        parsed = parse_experiment_findings('{"findings": "ratio 1.2", "warnings": ["slow"]}')
        assert parsed == {"findings": "ratio 1.2", "warnings": ["slow"]}

    def test_markdown_fence(self):
        parsed = parse_experiment_findings(
            '```json\n{"findings": "ratio 1.2", "warnings": []}\n```'
        )
        assert parsed["findings"] == "ratio 1.2"

    def test_empty_findings_rejected(self):
        with pytest.raises(ExperimentInvocationError):
            parse_experiment_findings('{"findings": "", "warnings": []}')

    def test_non_json_rejected(self):
        with pytest.raises(ExperimentInvocationError):
            parse_experiment_findings("the ratio is fine, trust me")

    def test_warnings_must_be_strings(self):
        with pytest.raises(ExperimentInvocationError):
            parse_experiment_findings('{"findings": "x", "warnings": [1]}')


# --- journal -----------------------------------------------------------------------


class TestJournal:
    def test_terminal_results_from_actual_code(self):
        journal = ComputationJournal()
        code = "print('ratio')"
        comp = uuid.uuid4()
        manifest = ArtifactManifest(
            path="r.json", extension=".json", size=3, sha256="a" * 64
        )
        journal.record_submit(_status(comp, status="running", artifacts=[manifest]), code)
        assert journal.terminal_results() == []  # still running
        assert journal.running_ids() == [comp]
        journal.record_status(_status(comp, status="succeeded", artifacts=[manifest]))
        results = journal.terminal_results()
        assert len(results) == 1
        assert results[0].code == code
        assert results[0].code_sha256 == hashlib.sha256(code.encode()).hexdigest()
        assert results[0].status == "succeeded"
        assert results[0].artifacts == [manifest]

    def test_running_computation_excluded_from_results(self):
        journal = ComputationJournal()
        comp = uuid.uuid4()
        journal.record_submit(_status(comp, status="running"), "print(1)")
        assert journal.terminal_results() == []


# --- tool handlers (mock Runner) ------------------------------------------------------


class _FakeRunner:
    """httpx.MockTransport handler emulating the Runner's tool plane."""

    def __init__(self):
        self.requests = []
        self.responses = {}  # (method, path) -> dict of httpx.Response kwargs

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key in self.responses:
            return httpx.Response(**self.responses[key])
        return httpx.Response(404, json={"detail": "no scripted response"})


@pytest.fixture
def tool_env():
    priv, pub, kid = _keys()
    tool_token = _grant(
        priv, kid, aud=AUD_RUNNER_TOOLS,
        scopes=(SCOPE_EXPERIMENT_SUBMIT, SCOPE_EXPERIMENT_STATUS, "experiment:read"),
    )
    fake = _FakeRunner()
    ctx = ExperimentToolContext(
        capability_token=_grant(priv, kid),
        tool_token=tool_token,
        public_keys=pub,
        tool_endpoint=ENDPOINT,
        experiment_invocation_id=EXP,
    )
    transport = httpx.MockTransport(fake.handler)
    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    token = set_experiment_tool_context(ctx)
    yield ctx, fake, patched_client, (priv, pub, kid)
    reset_experiment_tool_context(token)


class TestToolHandlers:
    def test_submit_records_journal_and_binds_context_experiment(self, tool_env, monkeypatch):
        import youwei_agent_runtime.experiment_tools as et

        ctx, fake, patched, (priv, pub, kid) = tool_env
        monkeypatch.setattr(et.httpx, "Client", patched)
        computation_id = uuid.uuid4()
        fake.responses[("POST", "/v1/experiment-computations")] = {"status_code": 202, "json": _status(computation_id, status="running").model_dump(mode="json")}
        raw = sandbox_submit_handler({
            "code": "print('ratio')",
            "expected_extensions": [".json"],
            "timeout_seconds": 30.0,
        })
        status = ExperimentComputationStatus.model_validate_json(raw)
        assert status.status == "running"
        # the request bound the CONTEXT's experiment id, not any model input
        sent = json.loads(fake.requests[0].read())
        assert sent["experiment_invocation_id"] == str(EXP)
        assert sent["code"] == "print('ratio')"
        assert ctx.journal.terminal_results() == []

    def test_status_updates_journal(self, tool_env, monkeypatch):
        import youwei_agent_runtime.experiment_tools as et

        ctx, fake, patched, (priv, pub, kid) = tool_env
        monkeypatch.setattr(et.httpx, "Client", patched)
        computation_id = uuid.uuid4()
        manifest = ArtifactManifest(
            path="r.json", extension=".json", size=3, sha256="a" * 64
        )
        ctx.journal.record_submit(
            _status(computation_id, status="running"), "print('ratio')"
        )
        fake.responses[
            ("GET", f"/v1/experiment-computations/{EXP}/{computation_id}")
        ] = {
            "status_code": 200,
            "json": _status(
                computation_id, status="succeeded", artifacts=[manifest]
            ).model_dump(mode="json"),
        }
        raw = sandbox_status_handler({"computation_id": str(computation_id)})
        status = ExperimentComputationStatus.model_validate_json(raw)
        assert status.status == "succeeded"
        assert ctx.journal.terminal_results()[0].artifacts == [manifest]

    def test_http_error_returns_payload_to_model(self, tool_env, monkeypatch):
        import youwei_agent_runtime.experiment_tools as et

        ctx, fake, patched, (priv, pub, kid) = tool_env
        monkeypatch.setattr(et.httpx, "Client", patched)
        fake.responses[("POST", "/v1/experiment-computations")] = {"status_code": 429, "json": {"detail": "experiment computation budget reached"}}
        raw = sandbox_submit_handler({
            "code": "print('ratio')",
            "expected_extensions": [".json"],
            "timeout_seconds": 30.0,
        })
        payload = json.loads(raw)
        assert payload["status_code"] == 429
        assert "budget reached" in payload["error"]
        assert ctx.journal.terminal_results() == []  # nothing recorded

    def test_artifact_read_returns_content(self, tool_env, monkeypatch):
        import youwei_agent_runtime.experiment_tools as et

        ctx, fake, patched, (priv, pub, kid) = tool_env
        monkeypatch.setattr(et.httpx, "Client", patched)
        computation_id = uuid.uuid4()
        content = '{"ratio": 1.32}'
        fake.responses[
            (
                "GET",
                f"/v1/experiment-computations/{EXP}/{computation_id}/artifacts/r.json",
            )
        ] = {
            "status_code": 200,
            "json": {
                "path": "r.json",
                "extension": ".json",
                "size": len(content),
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "content": content,
            },
        }
        raw = artifact_read_handler({
            "computation_id": str(computation_id), "path": "r.json",
        })
        assert json.loads(raw)["content"] == content

    def test_handlers_reject_calls_outside_a_run(self):
        with pytest.raises(ExperimentToolAuthorizationError):
            sandbox_submit_handler({
                "code": "x", "expected_extensions": [".json"],
                "timeout_seconds": 1.0,
            })

    def test_wrong_tool_audience_rejected(self, tool_env, monkeypatch):
        import youwei_agent_runtime.experiment_tools as et

        ctx, fake, patched, (priv, pub, kid) = tool_env
        monkeypatch.setattr(et.httpx, "Client", patched)
        # a runner-exec grant is not a tool grant
        ctx.tool_token = _grant(priv, kid, aud=AUD_RUNNER_EXEC, scopes=(SCOPE_EXPERIMENT_SUBMIT,))
        with pytest.raises(ExperimentToolAuthorizationError, match="audience"):
            sandbox_submit_handler({
                "code": "x", "expected_extensions": [".json"],
                "timeout_seconds": 1.0,
            })

    def test_missing_scope_rejected(self, tool_env, monkeypatch):
        import youwei_agent_runtime.experiment_tools as et

        ctx, fake, patched, (priv, pub, kid) = tool_env
        monkeypatch.setattr(et.httpx, "Client", patched)
        ctx.tool_token = _grant(priv, kid, aud=AUD_RUNNER_TOOLS, scopes=(SCOPE_EXPERIMENT_SUBMIT,))
        with pytest.raises(ExperimentToolAuthorizationError, match="scope"):
            sandbox_status_handler({"computation_id": str(uuid.uuid4())})

    def test_tool_token_bound_to_other_experiment_rejected(self, tool_env, monkeypatch):
        import youwei_agent_runtime.experiment_tools as et

        ctx, fake, patched, (priv, pub, kid) = tool_env
        monkeypatch.setattr(et.httpx, "Client", patched)
        ctx.tool_token = _grant(
            priv, kid, aud=AUD_RUNNER_TOOLS,
            scopes=(SCOPE_EXPERIMENT_SUBMIT,), invocation_id=uuid.uuid4(),
        )
        with pytest.raises(ExperimentToolAuthorizationError, match="another experiment"):
            sandbox_submit_handler({
                "code": "x", "expected_extensions": [".json"],
                "timeout_seconds": 1.0,
            })


# --- wire codec --------------------------------------------------------------------


class TestWireCodec:
    def _payload(self, priv, kid, *, runtime_token=None, tool_token=None):
        return {
            "capability_token": runtime_token or _grant(priv, kid),
            "tool_token": tool_token or _grant(
                priv, kid, aud=AUD_RUNNER_TOOLS,
                scopes=(SCOPE_EXPERIMENT_SUBMIT, SCOPE_EXPERIMENT_STATUS, "experiment:read"),
            ),
            "tool_endpoint": ENDPOINT,
            "request": _invocation_request().model_dump(mode="json"),
            "gateway": {"base_url": "http://gw", "api_key": "k"},
        }

    def test_happy_path_runs_and_encodes(self):
        import asyncio

        asyncio.run(self._happy_path())

    async def _happy_path(self):
        priv, pub, kid = _keys()
        seen = {}

        async def fake_run(question, manifest, config, **kwargs):
            seen["question"] = question
            seen["manifest"] = manifest
            seen["config"] = config
            seen["kwargs"] = kwargs
            from youwei_agent_runtime.experiment import ExperimentTurn
            from youwei_agent_runtime.runtime import UsageReport

            result = ExperimentResult(findings="ratio at median", warnings=[])
            return ExperimentTurn(
                result=result,
                usage=UsageReport(
                    source="unavailable", scope="unknown", complete=False
                ),
            )

        raw = await honor_experiment_request(
            self._payload(priv, kid),
            public_keys=pub,
            run_experiment_fn=fake_run,
            config_factory=lambda **kw: kw,
        )
        payload = json.loads(raw)
        assert payload["ok"] is True
        assert payload["result"]["findings"] == "ratio at median"
        assert payload["usage"]["source"] == "unavailable"
        assert seen["question"].question.startswith("20-session")
        assert seen["config"]["base_url"] == "http://gw"

    def test_wrong_audience_rejected(self):
        import asyncio

        asyncio.run(self._wrong_audience())

    async def _wrong_audience(self):
        priv, pub, kid = _keys()
        payload = self._payload(
            priv, kid, runtime_token=_grant(priv, kid, aud=AUD_RUNTIME_EXPERIMENT.replace("experiment", "research"))
            if False else _grant(priv, kid, aud=AUD_RUNNER_EXEC),
        )
        with pytest.raises(ExperimentInvocationError, match="audience"):
            await honor_experiment_request(
                payload, public_keys=pub,
                run_experiment_fn=None, config_factory=None,
            )

    def test_binding_mismatch_rejected(self):
        import asyncio

        asyncio.run(self._binding_mismatch())

    async def _binding_mismatch(self):
        priv, pub, kid = _keys()
        payload = self._payload(
            priv, kid, runtime_token=_grant(priv, kid, case_id=uuid.uuid4()),
        )
        with pytest.raises(ExperimentInvocationError, match="binding"):
            await honor_experiment_request(
                payload, public_keys=pub,
                run_experiment_fn=None, config_factory=None,
            )

    def test_missing_tool_token_rejected(self):
        import asyncio

        asyncio.run(self._missing_tool_token())

    async def _missing_tool_token(self):
        priv, pub, kid = _keys()
        payload = self._payload(priv, kid)
        del payload["tool_token"]
        with pytest.raises(ExperimentInvocationError, match="tool_token"):
            await honor_experiment_request(
                payload, public_keys=pub,
                run_experiment_fn=None, config_factory=None,
            )

    def test_expired_grant_rejected(self):
        import asyncio

        asyncio.run(self._expired_grant())

    async def _expired_grant(self):
        priv, pub, kid = _keys()
        expired = sign_research_token(
            priv, kid=kid, aud=AUD_RUNTIME_EXPERIMENT, scopes=(SCOPE_EXPERIMENT_RUN,),
            invocation_id=EXP, tenant_id=TENANT, run_id=RUN, job_id=JOB,
            attempt_no=1, case_id=CASE, evidence_sha256=EV_SHA,
            exec_config_version=EXEC_CONFIG,
            exp=datetime.now(UTC) - timedelta(minutes=1),
        )
        payload = self._payload(priv, kid, runtime_token=expired)
        with pytest.raises(ExperimentInvocationError):
            await honor_experiment_request(
                payload, public_keys=pub,
                run_experiment_fn=None, config_factory=None,
            )

    def test_check_capability_signature_check(self):
        priv, pub, kid = _keys()
        # a token signed by a DIFFERENT key, with a kid the container does not trust
        other_priv, _, _ = _keys()
        _, other_pub, other_kid = _keys()
        forged = sign_research_token(
            other_priv, kid=other_kid, aud=AUD_RUNTIME_EXPERIMENT,
            scopes=(SCOPE_EXPERIMENT_RUN,),
            invocation_id=EXP, tenant_id=TENANT, run_id=RUN, job_id=JOB,
            attempt_no=1, case_id=CASE, evidence_sha256=EV_SHA,
            exec_config_version=EXEC_CONFIG,
            exp=datetime.now(UTC) + timedelta(minutes=5),
        )
        with pytest.raises(ExperimentInvocationError):
            check_experiment_capability(pub, forged, _invocation_request())


# --- run_experiment with a fake agent -----------------------------------------------


class TestRunExperiment:
    def test_result_assembled_from_journal_not_model_report(self):
        from youwei_agent_runtime.runtime import ResearchConfig

        priv, pub, kid = _keys()
        config = ResearchConfig(
            base_url="http://gw", api_key="k", model="glm-5.3",
        )

        class FakeAgent:
            def __init__(self, *a, **kw):
                self.submitted = False

            def chat(self, brief):
                # the "model" submits one computation and reports findings
                ctx = current_experiment_tool_context()
                ctx.journal.record_submit(
                    _status(uuid.uuid4(), status="running"), "print('ratio')"
                )
                return json.dumps({
                    "findings": "ratio 1.2 backed by computation",
                    "warnings": [],
                })

        import youwei_agent_runtime.experiment as experiment_module

        original_make = experiment_module.make_experiment_agent
        experiment_module.make_experiment_agent = lambda cfg, **kw: FakeAgent()
        try:
            turn = __import__("asyncio").run(
                run_experiment(
                    _question(),
                    _manifest(),
                    config,
                    capability_token=_grant(priv, kid),
                    tool_token=_grant(
                        priv, kid, aud=AUD_RUNNER_TOOLS,
                        scopes=(SCOPE_EXPERIMENT_SUBMIT, SCOPE_EXPERIMENT_STATUS, "experiment:read"),
                    ),
                    tool_endpoint=ENDPOINT,
                    public_keys=pub,
                    experiment_invocation_id=EXP,
                )
            )
        finally:
            experiment_module.make_experiment_agent = original_make
        # the computation was still running at turn end -> excluded from the
        # result (it will surface via the Runner's receipts instead)
        assert turn.result.computations == []
        assert turn.result.findings == "ratio 1.2 backed by computation"

    def test_isolation_kwargs_enable_only_experiment_toolset(self):
        kwargs = experiment_isolation_kwargs()
        assert kwargs["enabled_toolsets"] == [EXPERIMENT_TOOLSET]
        assert kwargs["skip_memory"] is True
        assert kwargs["skip_context_files"] is True
        assert kwargs["skip_background_review"] is True
