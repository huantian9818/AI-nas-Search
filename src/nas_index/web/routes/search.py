from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.web.dependencies import get_session

router = APIRouter(prefix="/search")


@router.get(
    "",
    response_class=HTMLResponse,
)
def search(
    request: Request,
    q: str = Query(""),
    page: int = Query(1, ge=1),
    session: Session = Depends(get_session),
):
    results = EntryRepository(session).search(
        q,
        page=page,
        page_size=50,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="search.html",
        context={
            "query": q,
            "results": results,
        },
    )
