from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from nas_index.config import AppSettings
from nas_index.db import (
    create_database_engine,
    create_session_factory,
    init_database,
)
from nas_index.qnap.client import QnapClient
from nas_index.repositories.config import ConfigRepository
from nas_index.services.scan_manager import ScanManager
from nas_index.services.scanner import Scanner
from nas_index.web.routes import dashboard
from nas_index.web.routes import browse as browse_routes
from nas_index.web.routes import scans as scan_routes
from nas_index.web.routes import search as search_routes
from nas_index.web.routes import settings as settings_routes


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or AppSettings()
    engine = create_database_engine(settings.database_url)
    init_database(engine)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(title="QNAP File Index", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    web_dir = Path(__file__).parent
    app.state.templates = Jinja2Templates(
        directory=web_dir / "templates"
    )

    def scanner_factory() -> Scanner:
        with Session(engine) as session:
            connection = ConfigRepository(
                session
            ).get()
        if connection is None:
            raise RuntimeError(
                "NAS configuration is missing"
            )
        return Scanner(
            engine=engine,
            client_factory=lambda: QnapClient(
                connection,
                timeout_seconds=(
                    settings.qnap_timeout_seconds
                ),
                retry_attempts=(
                    settings.qnap_retry_attempts
                ),
            ),
            page_size=settings.scan_page_size,
            batch_size=settings.scan_batch_size,
        )

    app.state.scan_manager = ScanManager(
        scanner_factory
    )
    app.mount(
        "/static",
        StaticFiles(
            directory=web_dir / "static",
            check_dir=False,
        ),
        name="static",
    )
    app.include_router(dashboard.router)
    app.include_router(settings_routes.router)
    app.include_router(browse_routes.router)
    app.include_router(search_routes.router)
    app.include_router(scan_routes.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
