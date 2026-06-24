from contextlib import asynccontextmanager
from pathlib import Path
from secrets import token_bytes

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from nas_index.config import AppSettings, load_settings
from nas_index.db import (
    create_database_engine,
    create_session_factory,
    init_database,
)
from nas_index.logging import configure_logging
from nas_index.qnap.client import QnapClient
from nas_index.repositories.nas import NasRepository
from nas_index.repositories.syncs import SyncRepository
from nas_index.services.admin import AdminSessionStore
from nas_index.services.access import AccessSessionStore
from nas_index.services.connection_tests import ConnectionTestStore
from nas_index.services.process_monitor import ProcessMonitor
from nas_index.services.scan_rate import ScanRateTracker
from nas_index.services.scanner import Scanner
from nas_index.services.search_summary import OpenAIChatSearchSummarizer
from nas_index.services.sync_manager import SyncManager
from nas_index.services.thumbnails import ThumbnailService
from nas_index.time import format_beijing
from nas_index.web.routes import admin as admin_routes
from nas_index.web.routes import access as access_routes
from nas_index.web.routes import dashboard
from nas_index.web.routes import browse as browse_routes
from nas_index.web.routes import downloads as download_routes
from nas_index.web.routes import scans as scan_routes
from nas_index.web.routes import search as search_routes
from nas_index.web.routes import settings as settings_routes
from nas_index.web.routes import thumbnails as thumbnail_routes


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or load_settings()
    engine = create_database_engine(settings.database_url)
    init_database(engine)
    session_factory = create_session_factory(engine)
    configure_logging(settings.log_dir)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        with session_factory() as session:
            SyncRepository(
                session
            ).interrupt_running()
            session.commit()
        _app.state.sync_manager.start_scheduler()
        try:
            yield
        finally:
            await _app.state.sync_manager.stop_scheduler()
            engine.dispose()

    app = FastAPI(title="QNAP File Index", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.access_store = AccessSessionStore(
        ttl_seconds=settings.user_access_ttl_seconds
    )
    app.state.admin_store = AdminSessionStore(
        ttl_seconds=settings.admin_session_ttl_seconds
    )
    app.state.connection_test_store = ConnectionTestStore(
        ttl_seconds=300
    )
    app.state.search_summarizer = OpenAIChatSearchSummarizer(settings)
    app.state.process_monitor = ProcessMonitor()
    app.state.scan_rate_tracker = ScanRateTracker()
    app.state.thumbnail_service = ThumbnailService(
        cache_dir=settings.thumbnail_cache_dir,
        client_factory=lambda connection: QnapClient(
            connection,
            timeout_seconds=settings.qnap_timeout_seconds,
            retry_attempts=settings.qnap_retry_attempts,
        ),
    )
    app.state.search_summary_payload_secret = token_bytes(32)
    web_dir = Path(__file__).parent
    static_version = str(
        (web_dir / "static" / "app.css").stat().st_mtime_ns
    )
    app.state.templates = Jinja2Templates(
        directory=web_dir / "templates"
    )
    app.state.templates.env.globals["is_admin"] = (
        admin_routes.current_admin
    )
    app.state.templates.env.globals["current_access"] = (
        access_routes.current_access
    )
    app.state.templates.env.globals["format_time"] = format_beijing
    app.state.templates.env.globals["static_version"] = (
        static_version
    )

    def scanner_factory(nas_id: int) -> Scanner:
        with Session(engine) as session:
            connection = NasRepository(
                session
            ).connection_for_indexer(nas_id)
        if connection is None:
            raise RuntimeError(
                "NAS configuration is missing"
            )
        return Scanner(
            engine=engine,
            nas_id=nas_id,
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
            concurrency=settings.scan_concurrency,
            progress_interval_seconds=(
                settings.scan_progress_interval_seconds
            ),
            skip_recycle=settings.scan_skip_recycle,
        )

    app.state.sync_manager = SyncManager(
        scanner_factory,
        session_factory=session_factory,
        poll_seconds=settings.sync_scheduler_poll_seconds,
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
    app.include_router(admin_routes.router)
    app.include_router(access_routes.router)
    app.include_router(settings_routes.router)
    app.include_router(browse_routes.router)
    app.include_router(download_routes.router)
    app.include_router(search_routes.router)
    app.include_router(scan_routes.router)
    app.include_router(thumbnail_routes.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
