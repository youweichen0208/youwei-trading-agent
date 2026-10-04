
from fastapi import APIRouter
from youwei_runner.state import RunnerState

def build_router(state: RunnerState) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def health():
        return {
            "status": "ok",
            "contract_version": "sandbox-v1",
            "research": "agent-runtime-v1",
            "experiment": "experiment-v1" if state.experiment_store is not None else None,
        }


    return router
