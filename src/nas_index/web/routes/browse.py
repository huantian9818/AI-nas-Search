from dataclasses import dataclass
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from nas_index.models import Entry
from nas_index.repositories.entries import EntryRepository
from nas_index.types import UserAccess
from nas_index.web.dependencies import get_session
from nas_index.web.routes.access import current_access

router = APIRouter(prefix="/browse")


@dataclass(frozen=True)
class DirectoryTreeNode:
    entry: Entry
    children: list["DirectoryTreeNode"]
    is_current: bool
    is_ancestor: bool


def _normalize_path(value: str) -> str:
    parts = [
        part
        for part in PurePosixPath(
            value.replace("\\", "/")
        ).parts
        if part != "/"
    ]
    if not parts:
        return "/"
    return "/" + "/".join(parts)


def _expanded_paths(current_path: str) -> set[str]:
    normalized = _normalize_path(current_path)
    if normalized == "/":
        return set()
    expanded: set[str] = set()
    current = ""
    for part in PurePosixPath(normalized).parts:
        if part == "/":
            continue
        current = f"{current}/{part}"
        expanded.add(current)
    return expanded


def _build_tree(
    repository: EntryRepository,
    *,
    access: UserAccess,
    parent_path: str,
    current_path: str,
    expanded_paths: set[str],
) -> list[DirectoryTreeNode]:
    nodes: list[DirectoryTreeNode] = []
    for entry in repository.list_child_directories(
        access.nas_id,
        parent_path,
        allowed_share_paths=access.share_paths,
    ):
        is_current = entry.full_path == current_path
        should_expand = entry.full_path in expanded_paths
        nodes.append(
            DirectoryTreeNode(
                entry=entry,
                children=(
                    _build_tree(
                        repository,
                        access=access,
                        parent_path=entry.full_path,
                        current_path=current_path,
                        expanded_paths=expanded_paths,
                    )
                    if should_expand
                    else []
                ),
                is_current=is_current,
                is_ancestor=(
                    should_expand and not is_current
                ),
            )
        )
    return nodes


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
    access = current_access(request)
    if access is None:
        return RedirectResponse(
            "/access",
            status_code=303,
        )

    path = _normalize_path(path)
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
        access.nas_id,
        path,
        allowed_share_paths=access.share_paths,
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
            "tree_nodes": _build_tree(
                repository,
                access=access,
                parent_path="/",
                current_path=path,
                expanded_paths=_expanded_paths(path),
            ),
        },
    )
