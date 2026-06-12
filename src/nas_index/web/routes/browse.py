from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.web.dependencies import get_session

router = APIRouter(prefix="/browse")


@router.get(
    "",
    response_class=HTMLResponse,
    name="browse",
)
def browse(
    request: Request,
    path: str = Query("/"),
    selected: int | None = None,
    page: int = Query(1, ge=1),
    session: Session = Depends(get_session),
):
    repository = EntryRepository(session)
    if selected is not None:
        page = (
            repository.page_for_entry(
                selected,
                page_size=100,
            )
            or page
        )
    listing = repository.list_children(
        path,
        page=page,
        page_size=100,
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="browse.html",
        context={
            "path": path,
            "selected": selected,
            "listing": listing,
            "root_directories": (
                repository.list_child_directories("/")
            ),
        },
    )


@router.get(
    "/tree",
    response_class=HTMLResponse,
)
def tree_children(
    request: Request,
    path: str = Query("/"),
    session: Session = Depends(get_session),
):
    directories = EntryRepository(
        session
    ).list_child_directories(path)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="partials/tree_children.html",
        context={"directories": directories},
    )
