from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.scans import ScanRepository
from nas_index.web.dependencies import get_session

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
    file_count, directory_count = (
        EntryRepository(session).counts()
    )
    scans = ScanRepository(session)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "file_count": file_count,
            "directory_count": directory_count,
            "scan": scans.latest(),
            "last_successful_scan": (
                scans.last_successful()
            ),
        },
    )
