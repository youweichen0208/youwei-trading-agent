"""Core application assembly. Route modules own HTTP behavior."""
from fastapi import FastAPI
from youwei_core.config import Settings
from youwei_core.db.engine import make_engine
from youwei_core.api.routes import runs, data, research, campaigns, ops, admin


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="youwei-core", version="0.2.0")
    app.state.settings = settings
    app.state.engine = make_engine(
        settings.database_url,
        pool_size=settings.api_pool_size,
        max_overflow=settings.api_max_overflow,
    )

    for routes in (runs, data, research, campaigns, ops, admin):
        app.include_router(routes.router)
    return app
