from dataclasses import dataclass
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from nas_index.models import Entry
from nas_index.repositories.entries import EntryRepository
from nas_index.services.thumbnails import is_thumbnail_candidate
from nas_index.types import UserAccess
from nas_index.web.dependencies import get_session
from nas_index.web.routes.admin import current_admin
from nas_index.web.routes.access import (
    access_login_url,
    access_login_redirect,
    current_access,
)

router = APIRouter(prefix="/browse")


@dataclass(frozen=True)
class DirectoryTreeNode:
    entry: Entry
    children: list["DirectoryTreeNode"]
    is_current: bool
    is_ancestor: bool


@dataclass(frozen=True)
class BrowseSearchResult:
    entry: Entry
    relative_path: str
    browse_path: str
    selected_id: int | None


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


def _search_target(entry: Entry) -> tuple[str, int | None]:
    if entry.entry_type == "directory":
        return entry.full_path, None
    return entry.parent_path, entry.id


def _relative_result_path(
    current_path: str,
    entry: Entry,
) -> str:
    anchor_path = (
        entry.full_path
        if entry.entry_type == "directory"
        else entry.parent_path
    )
    if current_path == "/":
        relative = anchor_path.removeprefix("/")
    elif anchor_path == current_path:
        relative = ""
    else:
        relative = anchor_path.removeprefix(
            f"{current_path}/"
        )
    return relative or "当前目录"


@router.get(
    "",
    response_class=HTMLResponse,
    name="browse",
)
def browse(
    request: Request,
    path: str = Query("/"),
    q: str = Query(""),
    selected: int | None = None,
    page: int = Query(1, ge=1),
    session: Session = Depends(get_session),
):
    access = current_access(request)
    if access is None:
        if current_admin(request):
            return request.app.state.templates.TemplateResponse(
                request=request,
                name="browse.html",
                context={
                    "path": _normalize_path(path),
                    "search_mode": False,
                    "search_query": q.strip(),
                    "search_results": [],
                    "search_total": 0,
                    "selected": selected,
                    "listing": None,
                    "tree_nodes": [],
                    "is_thumbnail_candidate": (
                        is_thumbnail_candidate
                    ),
                    "access_required": True,
                    "access_login_url": access_login_url(
                        request
                    ),
                },
            )
        return access_login_redirect(request)

    path = _normalize_path(path)
    query = q.strip()
    repository = EntryRepository(session)
    search_mode = bool(query)
    if search_mode:
        search_page = repository.search_subtree(
            query,
            nas_id=access.nas_id,
            path=path,
            allowed_share_paths=access.share_paths,
        )
        search_results = []
        for entry in search_page.items:
            browse_path, selected_id = _search_target(
                entry
            )
            search_results.append(
                BrowseSearchResult(
                    entry=entry,
                    relative_path=_relative_result_path(
                        path,
                        entry,
                    ),
                    browse_path=browse_path,
                    selected_id=selected_id,
                )
            )
        listing = None
    else:
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
        search_page = None
        search_results = []
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="browse.html",
        context={
            "path": path,
            "search_mode": search_mode,
            "search_query": query,
            "search_results": search_results,
            "search_total": (
                0 if search_page is None else search_page.total
            ),
            "selected": selected,
            "listing": listing,
            "tree_nodes": _build_tree(
                repository,
                access=access,
                parent_path="/",
                current_path=path,
                expanded_paths=_expanded_paths(path),
            ),
            "is_thumbnail_candidate": is_thumbnail_candidate,
        },
    )
