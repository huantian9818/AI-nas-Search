from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from nas_index.repositories.nas import NasRepository
from nas_index.repositories.syncs import SyncRepository
from nas_index.services.sync_manager import NasSyncAlreadyRunning
from nas_index.web.dependencies import get_session

router = APIRouter(prefix="/scans")


@router.post("")
async def start_scan(
    request: Request,
    nas_id: int | None = Form(None),
    session: Session = Depends(get_session),
):
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
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="partials/scan_status.html",
        context={"scan": latest},
    )
