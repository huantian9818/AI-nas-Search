from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from nas_index.models import SyncRun
from nas_index.repositories.nas import NasRepository
from nas_index.repositories.syncs import SyncRepository
from nas_index.services.sync_manager import NasSyncAlreadyRunning
from nas_index.time import now_beijing
from nas_index.web.dependencies import get_session
from nas_index.web.routes.admin import admin_login_redirect

router = APIRouter(prefix="/scans")


@router.post("")
async def start_scan(
    request: Request,
    nas_id: int | None = Form(None),
    session: Session = Depends(get_session),
):
    redirect = admin_login_redirect(request, next_path="/")
    if redirect is not None:
        return redirect

    repository = NasRepository(session)
    if nas_id is None:
        servers = repository.list_servers()
        nas_id = servers[0].id if servers else None
    if nas_id is None or repository.get_server(nas_id) is None:
        return HTMLResponse(
            "请先保存 NAS 设置",
            status_code=409,
        )
    try:
        request.app.state.sync_manager.start_nas(nas_id)
    except NasSyncAlreadyRunning:
        return HTMLResponse(
            "同步任务正在运行",
            status_code=409,
        )
    return RedirectResponse(
        "/",
        status_code=303,
    )


@router.get(
    "/status",
    response_class=HTMLResponse,
)
def scan_status(
    request: Request,
    nas_id: int | None = None,
    session: Session = Depends(get_session),
):
    if nas_id is None:
        servers = NasRepository(session).list_servers()
        nas_id = servers[0].id if servers else None
    latest = (
        SyncRepository(session).latest_for_nas(nas_id)
        if nas_id is not None
        else None
    )
    process_monitor = getattr(
        request.app.state,
        "process_monitor",
        None,
    )
    process_usage = (
        process_monitor.sample()
        if process_monitor is not None
        else None
    )
    scan_rate_tracker = getattr(
        request.app.state,
        "scan_rate_tracker",
        None,
    )
    recent_entries_per_second = (
        scan_rate_tracker.sample(
            run_id=latest.id,
            processed_entries=latest.processed_entries,
        )
        if latest is not None
        and latest.status == "running"
        and scan_rate_tracker is not None
        else None
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="partials/scan_status.html",
        context={
            "scan": latest,
            "scan_metrics": _scan_metrics(latest),
            "recent_entries_per_second": (
                f"{recent_entries_per_second:.1f}"
                if recent_entries_per_second is not None
                else None
            ),
            "process_usage": process_usage,
            "format_bytes": _format_bytes,
        },
    )


def _scan_metrics(scan: SyncRun | None) -> dict[str, str | None]:
    if scan is None or scan.started_at is None:
        return {
            "elapsed": None,
            "entries_per_second": None,
        }

    end = (
        now_beijing()
        if scan.status == "running"
        else scan.finished_at or now_beijing()
    )
    elapsed_seconds = _elapsed_seconds(scan.started_at, end)
    entries_per_second = (
        scan.processed_entries / elapsed_seconds
        if elapsed_seconds > 0
        else None
    )
    return {
        "elapsed": _format_duration(elapsed_seconds),
        "entries_per_second": (
            f"{entries_per_second:.1f}"
            if entries_per_second is not None
            else None
        ),
    }


def _elapsed_seconds(start, end) -> float:
    if start.tzinfo is None and end.tzinfo is not None:
        end = end.replace(tzinfo=None)
    elif start.tzinfo is not None and end.tzinfo is None:
        start = start.replace(tzinfo=None)
    return max(
        0.0,
        (end - start).total_seconds(),
    )


def _format_duration(seconds: float) -> str:
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}小时{minutes}分{seconds}秒"
    if minutes:
        return f"{minutes}分{seconds}秒"
    return f"{seconds}秒"


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "未知"
    units = ("B", "KB", "MB", "GB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
