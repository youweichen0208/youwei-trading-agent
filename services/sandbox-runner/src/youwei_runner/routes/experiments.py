import asyncio
from datetime import UTC, datetime
import uuid
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from youwei_contracts.experiment import (
    ExperimentAuthorization,
    ExperimentComputationReceipt,
    ExperimentComputationRequest,
    ExperimentInvocationEnvelope,
    ExperimentInvocationStatus,
    experiment_invocation_digest,
)
from youwei_contracts.research_capability import (
    SCOPE_EXPERIMENT_ADMIN,
    SCOPE_EXPERIMENT_READ,
    SCOPE_EXPERIMENT_RUN,
    SCOPE_EXPERIMENT_RUN_STATUS,
    SCOPE_EXPERIMENT_STATUS,
    SCOPE_EXPERIMENT_SUBMIT,
)
from youwei_runner.experiment_store import (
    ExperimentStoreError,
    code_sha256,
    request_sha256,
    snapshot_sha256,
)
from fastapi import APIRouter
from youwei_runner.state import RunnerState, ExperimentRecord, ExperimentInvocationRecord

def build_router(state: RunnerState) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/experiment-authorizations")
    async def experiment_register(request: Request):
        cap = state.authorize_research(request, SCOPE_EXPERIMENT_ADMIN)
        store = state._require_experiment_store()
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > state.settings.max_request_bytes:
                raise HTTPException(413, "experiment authorization too large")
        try:
            auth = ExperimentAuthorization.model_validate_json(raw)
        except ValidationError:
            raise HTTPException(422, "invalid experiment-v1 authorization") from None
        if (cap.invocation_id != auth.experiment_invocation_id
                or cap.tenant_id != auth.tenant_id
                or cap.case_id != auth.case_id
                or cap.evidence_sha256 != auth.evidence_sha256
                or cap.exec_config_version != auth.exec_config_version):
            raise HTTPException(403, "experiment capability binding mismatch")
        try:
            store.register(auth)
        except ExperimentStoreError:
            raise HTTPException(
                409, "experiment already registered with different content"
            ) from None
        return {"status": "registered"}


    @router.delete("/v1/experiment-authorizations/{experiment_id}")
    async def experiment_terminate(experiment_id: uuid.UUID, request: Request):
        cap = state.authorize_research(request, SCOPE_EXPERIMENT_ADMIN)
        store = state._require_experiment_store()
        if cap.invocation_id != experiment_id:
            raise HTTPException(403, "experiment capability scope mismatch")
        # D2 requirement 3: reject new computations AND clean up containers.
        store.terminate(experiment_id)
        cancelled = [
            record for key, record in state.experiment_records.items()
            if key[0] == experiment_id
            and record.task is not None and not record.task.done()
        ]
        for record in cancelled:
            record.task.cancel()
        if cancelled:
            await asyncio.gather(*(r.task for r in cancelled), return_exceptions=True)
        # A task cancelled before its first step never runs its own receipt
        # update — belt and suspenders: any receipt still running for this
        # experiment becomes cancelled here.
        for record in cancelled:
            if record.receipt.status == "running":
                record.receipt = record.receipt.model_copy(
                    update={"status": "cancelled", "partial": False}
                )
                store.save_receipt(record.receipt)
        # Termination also reaches the experiment INSTANCE dispatch (S08c):
        # a running instance container is cancelled too — its tool calls
        # would all be rejected from here on anyway.
        instance = state.experiment_invocation_records.get(experiment_id)
        if (instance is not None and instance.task is not None
                and not instance.task.done()):
            instance.task.cancel()
            await asyncio.gather(instance.task, return_exceptions=True)
            if instance.view.status == "running":
                instance.view.status = "cancelled"
        return {"status": "terminated"}


    @router.get("/v1/experiment-authorizations/{experiment_id}/receipts")
    async def experiment_receipts(experiment_id: uuid.UUID, request: Request):
        cap = state.authorize_research(request, SCOPE_EXPERIMENT_ADMIN)
        store = state._require_experiment_store()
        if cap.invocation_id != experiment_id:
            raise HTTPException(403, "experiment capability scope mismatch")
        return [
            r.model_dump(mode="json")
            for r in store.receipts_for(experiment_id)
        ]


    @router.post("/v1/experiment-computations")
    async def experiment_submit(request: Request):
        cap = state.authorize_experiment_tool(request, SCOPE_EXPERIMENT_SUBMIT)
        store = state._require_experiment_store()
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > state.settings.max_request_bytes:
                raise HTTPException(413, "experiment computation too large")
        try:
            req = ExperimentComputationRequest.model_validate_json(raw)
        except ValidationError:
            raise HTTPException(422, "invalid experiment-v1 computation request") from None
        if req.experiment_invocation_id != cap.invocation_id:
            raise HTTPException(403, "computation is for another experiment")
        auth = store.authorization(req.experiment_invocation_id)
        if auth is None:
            raise HTTPException(404, "experiment not registered")
        state.check_experiment_binding(cap, auth)
        if store.is_terminated(req.experiment_invocation_id):
            raise HTTPException(403, "experiment terminated")
        digest = request_sha256(req.model_dump(mode="json"))
        existing = store.receipt(req.experiment_invocation_id, req.computation_id)
        if existing is not None:
            if existing.request_sha256 != digest:
                raise HTTPException(
                    409, "computation id already bound to another payload"
                )
            return JSONResponse(state._status_view(existing).model_dump(mode="json"))
        # D2 requirement 1: enforce the registered limits.
        if store.computation_count(req.experiment_invocation_id) >= auth.limits.max_computations:
            raise HTTPException(429, "experiment computation budget reached")
        if store.running_count(req.experiment_invocation_id) >= auth.limits.max_concurrent:
            raise HTTPException(429, "experiment concurrency limit reached")
        remaining = (auth.limits.max_total_duration_seconds
                     - store.used_duration(req.experiment_invocation_id))
        if req.timeout_seconds > remaining:
            raise HTTPException(
                429, "experiment duration budget insufficient for requested timeout"
            )
        if sum(r.task is not None and not r.task.done()
               for r in state.experiment_records.values()) >= state.settings.max_parallel:
            raise HTTPException(429, "runner at capacity")
        receipt = ExperimentComputationReceipt(
            experiment_invocation_id=req.experiment_invocation_id,
            computation_id=req.computation_id,
            request_sha256=digest,
            code_sha256=code_sha256(req.code),
            image=state.settings.image,
            snapshot_sha256=snapshot_sha256(auth.snapshot),
            status="running",
            duration_seconds=0.0,
        )
        store.save_receipt(receipt)
        record = ExperimentRecord(req, receipt)
        state.experiment_records[(req.experiment_invocation_id, req.computation_id)] = record
        record.task = asyncio.create_task(state.execute_experiment(record, auth))
        return JSONResponse(state._status_view(receipt).model_dump(mode="json"), status_code=202)


    @router.get("/v1/experiment-computations/{experiment_id}/{computation_id}")
    async def experiment_status(
        experiment_id: uuid.UUID, computation_id: uuid.UUID, request: Request
    ):
        _, receipt = state._find_experiment_receipt(
            experiment_id, computation_id, request, SCOPE_EXPERIMENT_STATUS
        )
        return state._status_view(receipt)


    @router.get(
        "/v1/experiment-computations/{experiment_id}/{computation_id}/artifacts/{artifact_path:path}"
    )
    async def experiment_read(
        experiment_id: uuid.UUID,
        computation_id: uuid.UUID,
        artifact_path: str,
        request: Request,
    ):
        store, receipt = state._find_experiment_receipt(
            experiment_id, computation_id, request, SCOPE_EXPERIMENT_READ
        )
        manifest = next(
            (a for a in receipt.artifacts if a.path == artifact_path), None
        )
        if manifest is None:
            raise HTTPException(404, "artifact not in receipt")
        try:
            artifact = store.read_artifact(experiment_id, computation_id, manifest)
        except ExperimentStoreError:
            raise HTTPException(410, "artifact unreadable (missing or hash mismatch)") from None
        return artifact


    @router.post("/v1/experiment-invocations")
    async def experiment_invocation_submit(request: Request):
        cap = state.authorize_research(request, SCOPE_EXPERIMENT_RUN)
        store = state._require_experiment_store()
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > state.settings.max_request_bytes:
                raise HTTPException(413, "experiment invocation too large")
        try:
            envelope = ExperimentInvocationEnvelope.model_validate_json(raw)
        except ValidationError:
            raise HTTPException(422, "invalid experiment-v1 envelope") from None
        req = envelope.request
        digest = experiment_invocation_digest(req)
        if (
            cap.invocation_id != req.experiment_invocation_id
            or cap.tenant_id != req.tenant_id
            or cap.run_id != req.run_id
            or cap.job_id != req.job_id
            or cap.attempt_no != req.attempt_no
            or cap.case_id != req.case_id
            or cap.evidence_sha256 != req.evidence_sha256
            or cap.exec_config_version != req.exec_config_version
        ):
            raise HTTPException(403, "experiment capability binding mismatch")
        # The experiment must be REGISTERED here before an instance may run:
        # the tool plane binds every computation to the registered
        # authorization (limits + the Runner-injected snapshot).
        auth = store.authorization(req.experiment_invocation_id)
        if auth is None:
            raise HTTPException(404, "experiment not registered")
        if store.is_terminated(req.experiment_invocation_id):
            raise HTTPException(403, "experiment terminated")
        if (
            auth.tenant_id != req.tenant_id
            or auth.run_id != req.run_id
            or auth.job_id != req.job_id
            or auth.attempt_no != req.attempt_no
            or auth.case_id != req.case_id
            or auth.evidence_sha256 != req.evidence_sha256
            or auth.exec_config_version != req.exec_config_version
        ):
            raise HTTPException(403, "experiment invocation does not match its registration")
        key = req.experiment_invocation_id
        existing = state.experiment_invocation_records.get(key)
        if existing is not None:
            if existing.view.request_sha256 != digest:
                raise HTTPException(409, "invocation already bound to another payload")
            existing.expires_at = max(existing.expires_at, cap.exp)
            return JSONResponse(existing.view.model_dump(mode="json"))
        running_instances = sum(
            r.task is not None and not r.task.done()
            for r in state.experiment_invocation_records.values()
        )
        if running_instances >= state.settings.max_parallel:
            raise HTTPException(429, "experiment runner at capacity")
        if len(state.experiment_invocation_records) >= state.settings.max_records:
            for old_key, old in list(state.experiment_invocation_records.items()):
                if (old.task is not None and old.task.done()
                        and old.expires_at < datetime.now(UTC)):
                    del state.experiment_invocation_records[old_key]
            if len(state.experiment_invocation_records) >= state.settings.max_records:
                raise HTTPException(503, "experiment invocation capacity reached")
        record = ExperimentInvocationRecord(
            req,
            envelope.runtime_token,
            envelope.tool_token,
            ExperimentInvocationStatus(
                invocation_id=req.experiment_invocation_id,
                request_sha256=digest,
                status="running",
            ),
            cap.exp,
        )
        state.experiment_invocation_records[key] = record
        record.task = asyncio.create_task(state.execute_experiment_instance(record))
        return JSONResponse(record.view.model_dump(mode="json"), status_code=202)


    @router.get("/v1/experiment-invocations/{experiment_id}")
    async def experiment_invocation_status(
        experiment_id: uuid.UUID, request: Request
    ):
        cap = state.authorize_research(request, SCOPE_EXPERIMENT_RUN_STATUS)
        if cap.invocation_id != experiment_id:
            raise HTTPException(403, "experiment capability scope mismatch")
        record = state.experiment_invocation_records.get(experiment_id)
        if record is None:
            raise HTTPException(404, "experiment invocation unavailable; recover through Core")
        record.expires_at = max(record.expires_at, cap.exp)
        return record.view


    return router
