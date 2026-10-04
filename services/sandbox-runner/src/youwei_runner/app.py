"""Runner application assembly; no execution policy lives in route wiring."""
from fastapi import FastAPI
from youwei_runner.settings import RunnerSettings
from youwei_runner.state import RunnerState, Executor, ResearchExecutor, ExperimentInstanceExecutor
from youwei_runner.routes import sandbox, research, experiments, health


def create_app(
    settings: RunnerSettings,
    *,
    executor: Executor | None = None,
    research_executor: ResearchExecutor | None = None,
    experiment_instance_executor: ExperimentInstanceExecutor | None = None,
) -> FastAPI:
    state = RunnerState(settings, executor=executor, research_executor=research_executor,
                        experiment_instance_executor=experiment_instance_executor)
    app = FastAPI(lifespan=state.lifespan)
    for routes in (sandbox, research, experiments, health):
        app.include_router(routes.build_router(state))
    return app
