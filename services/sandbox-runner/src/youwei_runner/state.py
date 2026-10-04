import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Awaitable, Callable
import uuid
from fastapi import HTTPException, Request
from youwei_contracts.capability import CapabilityError, verify_capability
from youwei_contracts.sandbox import SandboxRequest, ExecutionResult, ExecutionStatus
from youwei_contracts.agent_runtime import (
    ResearchInvocationRequest,
    ResearchInvocationResult,
    ResearchInvocationStatus,
)
from youwei_contracts.experiment import (
    ArtifactManifest,
    ExperimentAuthorization,
    ExperimentComputationReceipt,
    ExperimentComputationRequest,
    ExperimentComputationStatus,
    ExperimentInvocationRequest,
    ExperimentInvocationResult,
    ExperimentInvocationStatus,
)
from youwei_contracts.research_capability import (
    AUD_RUNNER_EXEC,
    AUD_RUNNER_TOOLS,
    ResearchCapabilityError,
    verify_research_token,
)
from youwei_runner.experiment_store import ExperimentStore


@dataclass
class Record:
    request: SandboxRequest
    view: ExecutionStatus
    expires_at: datetime
    weight: int = 0
    task: asyncio.Task | None = None


@dataclass
class ResearchRecord:
    request: ResearchInvocationRequest
    runtime_token: str
    view: ResearchInvocationStatus
    expires_at: datetime
    task: asyncio.Task | None = None


@dataclass
class ExperimentRecord:
    """In-memory handle for one running/finished experiment computation. The
    durable truth is the persistent receipt (ExperimentStore); this record
    exists only to hold the asyncio task and the latest receipt object."""

    request: ExperimentComputationRequest
    receipt: ExperimentComputationReceipt
    task: asyncio.Task | None = None


Executor = Callable[[SandboxRequest], Awaitable[ExecutionResult]]


ResearchExecutor = Callable[
    [ResearchInvocationRequest], Awaitable[ResearchInvocationResult]
]


ExperimentInstanceExecutor = Callable[
    [ExperimentInvocationRequest, str, str], Awaitable[ExperimentInvocationResult]
]


@dataclass
class ExperimentInvocationRecord:
    """In-memory handle for one running/finished experiment instance
    dispatch (the container turn). The durable experiment evidence lives in
    the persistent store (authorizations + computation receipts); this record
    only carries the live task and the latest status view."""

    request: ExperimentInvocationRequest
    runtime_token: str
    tool_token: str
    view: ExperimentInvocationStatus
    expires_at: datetime
    task: asyncio.Task | None = None


class RunnerState:
    """Per-application execution handles, authorization and task lifecycle.

    Durable experiment evidence stays in ExperimentStore; these maps never
    replace Core's authoritative jobs and fencing.
    """

    def __init__(self, settings, *, executor=None, research_executor=None,
                 experiment_instance_executor=None):
        self.settings = settings
        self.executor = executor
        self.research_executor = research_executor
        self.experiment_instance_executor = experiment_instance_executor
        self.records = {}
        self.research_records = {}
        self.experiment_records = {}
        self.experiment_invocation_records = {}
        self.experiment_store = (
            ExperimentStore(settings.experiment_store_dir)
            if settings.experiment_store_dir else None
        )
        if self.experiment_store is not None:
            self.experiment_store.open()

    @asynccontextmanager
    async def lifespan(self, app):
        if self.executor is None:
            from youwei_runner.execution import remove_orphaned_containers
            await remove_orphaned_containers()
        try:
            yield
        finally:
            tasks = [r.task for records in (
                self.records, self.research_records, self.experiment_records,
                self.experiment_invocation_records,
            ) for r in records.values() if r.task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def prune(self):
        for key, record in list(self.records.items()):
            if record.task is not None and record.task.done() and record.expires_at < datetime.now(UTC):
                del self.records[key]


    def weight(self, value):
        # Budget UTF-8 serialized content conservatively for Python strings and
        # bookkeeping. Also bounded by the Runner container's hard memory limit.
        return 4 * len(value.model_dump_json().encode("utf-8"))


    def authorize(self, request: Request):
        raw = request.headers.get("authorization", "")
        if not raw.startswith("Bearer "):
            raise HTTPException(401, "runner capability required")
        try:
            return verify_capability(self.settings.secret, raw[7:])
        except (CapabilityError, ValueError, KeyError, TypeError):
            raise HTTPException(403, "invalid runner capability") from None


    def check_binding(self, cap, job, digest):
        if (cap.job_id != job.job_id or cap.attempt_no != job.attempt_no
                or cap.tenant_id != job.tenant_id
                or "sandbox:execute" not in cap.scopes
                or f"payload:{digest}" not in cap.scopes):
            raise HTTPException(403, "runner capability scope mismatch")


    async def execute(self, record):
        work = None
        try:
            if self.executor is None:
                from youwei_runner.execution import execute_request
                work = asyncio.create_task(execute_request(record.request, self.settings))
            else:
                work = asyncio.create_task(self.executor(record.request))
            while not work.done():
                if record.expires_at <= datetime.now(UTC):
                    raise asyncio.CancelledError
                await asyncio.wait({work}, timeout=0.05)
            result = work.result()
            extra = self.weight(result)
            if sum(r.weight for r in self.records.values()) + extra > self.settings.max_cached_bytes:
                raise RuntimeError("runner result exceeds cache budget")
            record.weight += extra
            record.view.result = result
            record.view.status = "succeeded"
        except asyncio.CancelledError:
            record.view.status = "cancelled"
        except Exception as exc:
            record.view.status = "failed"
            record.view.error = str(exc)[:500]
        finally:
            if work is not None and not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)


    def find_record(self, job_id, attempt_no, request):
        cap = self.authorize(request)
        self.prune()
        if str(cap.job_id) != job_id or cap.attempt_no != attempt_no:
            raise HTTPException(403, "runner capability scope mismatch")
        record = self.records.get((cap.job_id, attempt_no))
        if record is None:
            raise HTTPException(404, "execution unavailable; recover through Core")
        self.check_binding(cap, record.request, record.view.request_sha256)
        record.expires_at = max(record.expires_at, cap.exp)
        return record


    def authorize_research(self, request: Request, scope: str):
        raw = request.headers.get("authorization", "")
        if not raw.startswith("Bearer "):
            raise HTTPException(401, "research capability required")
        try:
            cap = verify_research_token(self.settings.agent_runtime_public_keys, raw[7:])
        except ResearchCapabilityError:
            raise HTTPException(403, "invalid research capability") from None
        if cap.aud != AUD_RUNNER_EXEC:
            raise HTTPException(403, "research capability has wrong audience")
        if scope not in cap.scopes:
            raise HTTPException(403, f"research capability missing {scope} scope")
        return cap


    async def execute_research(self, record: ResearchRecord):
        work = None
        try:
            if self.research_executor is None:
                from youwei_runner.research import execute_research_request
                from youwei_runner.research import build_research_config

                config = build_research_config(self.settings)
                # The app layer already authorized the request (aud=runner-exec);
                # the container grant (aud=runtime-research) is passed through so
                # the research container can verify it independently.
                work = asyncio.create_task(
                    execute_research_request(
                        record.request, config, capability_token=record.runtime_token
                    )
                )
            else:
                work = asyncio.create_task(self.research_executor(record.request))
            while not work.done():
                if record.expires_at <= datetime.now(UTC):
                    raise asyncio.CancelledError
                await asyncio.wait({work}, timeout=0.05)
            result = work.result()
            record.view.result = result
            record.view.status = "succeeded"
        except asyncio.CancelledError:
            record.view.status = "cancelled"
        except Exception as exc:
            record.view.status = "failed"
            record.view.error = str(exc)[:500]
        finally:
            if work is not None and not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)


    def find_research_record(self, invocation_id, request, scope):
        cap = self.authorize_research(request, scope)
        if cap.invocation_id != invocation_id:
            raise HTTPException(403, "research capability scope mismatch")
        record = self.research_records.get(invocation_id)
        if record is None:
            raise HTTPException(404, "research invocation unavailable; recover through Core")
        record.expires_at = max(record.expires_at, cap.exp)
        return record


    def authorize_experiment_tool(self, request: Request, scope: str):
        raw = request.headers.get("authorization", "")
        if not raw.startswith("Bearer "):
            raise HTTPException(401, "experiment capability required")
        try:
            cap = verify_research_token(self.settings.agent_runtime_public_keys, raw[7:])
        except ResearchCapabilityError:
            raise HTTPException(403, "invalid experiment capability") from None
        if cap.aud != AUD_RUNNER_TOOLS:
            raise HTTPException(403, "experiment capability has wrong audience")
        if scope not in cap.scopes:
            raise HTTPException(403, f"experiment capability missing {scope} scope")
        return cap


    def check_experiment_binding(self, cap, auth: ExperimentAuthorization):
        if (cap.tenant_id != auth.tenant_id
                or cap.case_id != auth.case_id
                or cap.evidence_sha256 != auth.evidence_sha256
                or cap.exec_config_version != auth.exec_config_version):
            raise HTTPException(403, "experiment capability binding mismatch")


    def _require_experiment_store(self) -> ExperimentStore:
        if self.experiment_store is None:
            raise HTTPException(503, "experiment entry not configured")
        return self.experiment_store


    def _status_view(self, receipt: ExperimentComputationReceipt) -> ExperimentComputationStatus:
        return ExperimentComputationStatus(
            experiment_invocation_id=receipt.experiment_invocation_id,
            computation_id=receipt.computation_id,
            status=receipt.status,
            partial=receipt.partial,
            stdout_excerpt=receipt.stdout_excerpt,
            stderr_excerpt=receipt.stderr_excerpt,
            artifacts=receipt.artifacts,
            error=receipt.error,
            duration_seconds=receipt.duration_seconds,
        )


    async def execute_experiment(self, record: ExperimentRecord, auth: ExperimentAuthorization):
        """Run one computation in the sandbox executor with the Runner-injected
        snapshot; every transition lands in the persistent receipt."""
        req = record.request
        sandbox_request = SandboxRequest(
            job_id=auth.job_id, run_id=auth.run_id,
            attempt_id=auth.attempt_id, attempt_no=auth.attempt_no,
            tenant_id=auth.tenant_id,
            script=req.code, argv=req.argv, env=req.env,
            snapshot=auth.snapshot,
        )
        work = None
        started = time.monotonic()

        def finish(**updates):
            record.receipt = record.receipt.model_copy(update=updates)
            self.experiment_store.save_receipt(record.receipt)

        try:
            if self.executor is not None:
                work = asyncio.create_task(self.executor(sandbox_request))
            else:
                from youwei_runner.execution import execute_request
                work = asyncio.create_task(execute_request(sandbox_request, self.settings))
            result = await asyncio.wait_for(work, timeout=req.timeout_seconds)
            budget = (auth.limits.max_artifact_bytes
                      - self.experiment_store.artifact_bytes_used(req.experiment_invocation_id))
            if sum(a.size for a in result.artifacts) > budget:
                finish(status="failed", partial=False,
                       error="experiment artifact budget exceeded",
                       duration_seconds=time.monotonic() - started)
                return
            self.experiment_store.write_artifacts(
                req.experiment_invocation_id, req.computation_id, result.artifacts)
            manifests = [
                ArtifactManifest(path=a.path, extension=a.extension,
                                 size=a.size, sha256=a.sha256)
                for a in result.artifacts
            ]
            duration = time.monotonic() - started
            if result.exit_code == 0:
                finish(status="succeeded", partial=False, artifacts=manifests,
                       stdout_excerpt=result.stdout[:4000],
                       stderr_excerpt=result.stderr[:4000],
                       duration_seconds=duration)
            else:
                finish(status="failed", partial=bool(manifests), artifacts=manifests,
                       error=f"sandbox exited {result.exit_code}",
                       stdout_excerpt=result.stdout[:4000],
                       stderr_excerpt=result.stderr[:4000],
                       duration_seconds=duration)
        except asyncio.TimeoutError:
            finish(status="timeout", partial=False,
                   error=f"computation exceeded {req.timeout_seconds}s",
                   duration_seconds=time.monotonic() - started)
        except asyncio.CancelledError:
            finish(status="cancelled", partial=False,
                   duration_seconds=time.monotonic() - started)
        except Exception as exc:
            finish(status="failed", partial=False,
                   error=f"{type(exc).__name__}: {exc}"[:2000],
                   duration_seconds=time.monotonic() - started)
        finally:
            if work is not None and not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)


    def _find_experiment_receipt(self, 
        experiment_id: uuid.UUID, computation_id: uuid.UUID, request: Request, scope: str
    ):
        cap = self.authorize_experiment_tool(request, scope)
        store = self._require_experiment_store()
        if cap.invocation_id != experiment_id:
            raise HTTPException(403, "experiment capability scope mismatch")
        auth = store.authorization(experiment_id)
        if auth is None:
            raise HTTPException(404, "experiment not registered")
        self.check_experiment_binding(cap, auth)
        receipt = store.receipt(experiment_id, computation_id)
        if receipt is None:
            raise HTTPException(404, "computation unknown")
        return store, receipt


    async def execute_experiment_instance(self, record: ExperimentInvocationRecord):
        work = None
        try:
            if self.experiment_instance_executor is not None:
                work = asyncio.create_task(
                    self.experiment_instance_executor(
                        record.request, record.runtime_token, record.tool_token
                    )
                )
            else:
                from youwei_runner.experiment_instance import (
                    build_experiment_run_config,
                    execute_experiment_invocation,
                )

                config = build_experiment_run_config(self.settings)
                work = asyncio.create_task(
                    execute_experiment_invocation(
                        record.request,
                        config,
                        runtime_token=record.runtime_token,
                        tool_token=record.tool_token,
                    )
                )
            while not work.done():
                if record.expires_at <= datetime.now(UTC):
                    raise asyncio.CancelledError
                await asyncio.wait({work}, timeout=0.05)
            result = work.result()
            if result.ok and result.result is not None:
                record.view.result = result
                record.view.status = "succeeded"
            else:
                record.view.status = "failed"
                record.view.error = (result.error or "experiment instance failed")[:500]
                record.view.result = result
        except asyncio.CancelledError:
            record.view.status = "cancelled"
        except Exception as exc:
            record.view.status = "failed"
            record.view.error = str(exc)[:500]
        finally:
            if work is not None and not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)


