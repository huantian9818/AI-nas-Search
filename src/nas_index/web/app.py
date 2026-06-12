from contextlib import asynccontextmanager

from fastapi import FastAPI

from nas_index.config import AppSettings
from nas_index.db import create_database_engine, create_session_factory


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or AppSettings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(title="QNAP File Index", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
