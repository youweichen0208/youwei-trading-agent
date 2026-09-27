"""Core's only sandbox execution transport. No local Docker fallback."""
import asyncio
from typing import Awaitable, Callable
import httpx
from pydantic import ValidationError
from youwei_contracts.sandbox import ExecutionResult, ExecutionStatus, SandboxRequest, request_digest


class RunnerError(Exception):
    pass


class RunnerClient:
    def __init__(self, base_url: str, *, transport=None, poll_seconds: float = 0.2):
        if not base_url:
            raise ValueError("runner URL required; no in-process fallback")
        self.http = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=15.0, trust_env=False)
        self.poll_seconds = poll_seconds

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.http.aclose()

    async def aclose(self):
        await self.http.aclose()

    async def execute(self, request: SandboxRequest, token: Callable[[], Awaitable[str]]) -> ExecutionResult:
        path = f"/v1/executions/{request.job_id}/{request.attempt_no}"
        digest = request_digest(request)
        last_token = await token()

        async def call(method, url, body=None):
            nonlocal last_token
            last_token = await token()
            async with self.http.stream(method, url, json=body,
                    headers={"Authorization": f"Bearer {last_token}"}) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 128 * 1024 * 1024:
                        raise RunnerError("runner response too large")
                if response.status_code not in (200, 202):
                    raise RunnerError(f"runner {method} failed ({response.status_code})")
            try:
                view = ExecutionStatus.model_validate_json(data)
            except ValidationError as exc:
                raise RunnerError("invalid runner result contract") from exc
            if (view.job_id != request.job_id or view.attempt_no != request.attempt_no
                    or view.request_sha256 != digest):
                raise RunnerError("runner result binding mismatch")
            return view

        try:
            view = await call("POST", "/v1/executions", request.model_dump(mode="json"))
            while view.status == "running":
                await asyncio.sleep(self.poll_seconds)
                view = await call("GET", path)
            if view.status != "succeeded" or view.result is None:
                raise RunnerError(view.error or f"runner execution {view.status}")
            return view.result
        except BaseException:
            # Includes a submit whose response was lost. If Core already cancelled
            # the attempt use the last scoped token; expiry is the fallback stop.
            try:
                await self.http.delete(path, headers={"Authorization": f"Bearer {last_token}"}, timeout=3.0)
            except httpx.HTTPError:
                pass
            raise
