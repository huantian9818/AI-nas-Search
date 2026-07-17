from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.nas import NasRepository
from nas_index.repositories.syncs import SyncRepository
from nas_index.web.dependencies import get_session
from nas_index.web.routes.admin import current_admin
from nas_index.web.routes.access import (
    access_login_redirect,
    current_access,
)

router = APIRouter()


@router.get(
    "/",
    response_class=HTMLResponse,
    name="dashboard",
)
def dashboard(
    request: Request,
    session: Session = Depends(get_session),
):
    if (
        current_access(request) is None
        and not current_admin(request)
    ):
        return access_login_redirect(request)

    file_count, directory_count = (
        EntryRepository(session).counts()
    )
    servers = NasRepository(session).list_servers()
    syncs = SyncRepository(session)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "file_count": file_count,
            "directory_count": directory_count,
            "servers": servers,
            "syncs_by_nas": {
                server.id: syncs.latest_for_nas(server.id)
                for server in servers
            },
            "scan": syncs.latest(),
            "last_successful_scan": (
                syncs.last_successful()
            ),
        },
    )
