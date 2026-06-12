from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from nas_index.repositories.config import ConfigRepository
from nas_index.repositories.scans import ScanRepository
from nas_index.services.scan_manager import ScanAlreadyRunning
from nas_index.web.dependencies import get_session

router = APIRouter(prefix="/scans")


@router.post("")
async def start_scan(
    request: Request,
    session: Session = Depends(get_session),
):
    if ConfigRepository(session).get() is None:
        return HTMLResponse(
            "请先保存 NAS 设置",
            status_code=409,
        )
    try:
        request.app.state.scan_manager.start()
    except ScanAlreadyRunning:
        return HTMLResponse(
            "扫描任务正在运行",
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
    session: Session = Depends(get_session),
):
    latest = ScanRepository(session).latest()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="partials/scan_status.html",
        context={"scan": latest},
    )
