import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Callable
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from markupsafe import Markup, escape
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from nas_index.models import Entry
from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.entries import Page
from nas_index.services.search_summary import SearchSummaryContext
from nas_index.services.search_summary import SearchSummaryDirectory
from nas_index.services.search_summary import SearchSummaryItem
from nas_index.services.search_summary import SearchSummaryPayloadError
from nas_index.services.search_summary import SearchSummaryUnavailable
from nas_index.services.search_summary import load_search_summary_payload
from nas_index.services.search_summary import sign_search_summary_payload
from nas_index.types import UserAccess
from nas_index.web.routes.browse import _expanded_paths
from nas_index.web.routes.browse import _normalize_path
from nas_index.web.dependencies import get_session
from nas_index.web.routes.access import current_access

router = APIRouter(prefix="/search")


@dataclass(frozen=True)
class BreadcrumbPart:
    name: str
    path: str


@dataclass(frozen=True)
class SearchResultGroup:
    path: str
    breadcrumbs: list[BreadcrumbPart]
    items: list[Entry]
    is_selected: bool


@dataclass(frozen=True)
class SearchTreeNode:
    entry: Entry
    children: list["SearchTreeNode"]
    is_current: bool
    is_ancestor: bool
    is_match: bool
    is_selected: bool
    is_result: bool


class SearchSummaryRequest(BaseModel):
    payload: str = Field(..., min_length=1)
    signature: str = Field(..., min_length=1)
    question: str = Field(..., max_length=500)


def _build_highlighter(
    query: str,
) -> Callable[[str], Markup]:
    terms = [
        term
        for term in re.split(r"\s+", query.strip())
        if term
    ]
    if not terms:
        return lambda value: Markup(escape(value))

    unique_terms: list[str] = []
    seen: set[str] = set()
    for term in sorted(
        terms,
        key=len,
        reverse=True,
    ):
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_terms.append(term)

    pattern = re.compile(
        "|".join(
            re.escape(term) for term in unique_terms
        ),
        re.IGNORECASE,
    )

    def highlight(value: str) -> Markup:
        if not value:
            return Markup("")

        chunks: list[Markup] = []
        last_index = 0
        for match in pattern.finditer(value):
            if match.start() > last_index:
                chunks.append(
                    Markup(
                        escape(
                            value[
                                last_index : match.start()
                            ]
                        )
                    )
                )
            chunks.append(
                Markup("<mark>")
                + Markup(escape(match.group(0)))
                + Markup("</mark>")
            )
            last_index = match.end()
        if last_index < len(value):
            chunks.append(
                Markup(
                    escape(value[last_index:])
                )
            )
        return Markup("").join(chunks)

    return highlight


def _format_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "—"

    size = float(size_bytes)
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            if size >= 100 or size.is_integer():
                return f"{size:.0f} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{int(size_bytes)} B"


def _format_modified(
    modified_at: datetime | None,
) -> str:
    if modified_at is None:
        return "—"
    return modified_at.strftime("%Y-%m-%d %H:%M")


def _breadcrumb_parts(
    path: str,
) -> list[BreadcrumbPart]:
    normalized = _normalize_path(path)
    if normalized == "/":
        return []

    current = ""
    breadcrumbs: list[BreadcrumbPart] = []
    for part in PurePosixPath(normalized).parts:
        if part == "/":
            continue
        current = f"{current}/{part}"
        breadcrumbs.append(
            BreadcrumbPart(name=part, path=current)
        )
    return breadcrumbs


def _select_result(
    items: list[Entry],
    selected_id: int | None,
) -> Entry | None:
    if not items:
        return None
    if selected_id is not None:
        for item in items:
            if item.id == selected_id:
                return item
    return items[0]


def _focus_directory_path(
    entry: Entry,
) -> str:
    if entry.entry_type == "directory":
        return entry.full_path
    return entry.parent_path


def _group_results(
    items: list[Entry],
    selected_id: int | None,
) -> list[SearchResultGroup]:
    grouped: dict[str, list[Entry]] = {}
    for item in items:
        grouped.setdefault(
            item.parent_path,
            [],
        ).append(item)

    groups = [
        SearchResultGroup(
            path=path,
            breadcrumbs=_breadcrumb_parts(path),
            items=group_items,
            is_selected=any(
                item.id == selected_id
                for item in group_items
            ),
        )
        for path, group_items in grouped.items()
    ]
    selected_groups = [
        group for group in groups if group.is_selected
    ]
    other_groups = [
        group for group in groups if not group.is_selected
    ]
    return selected_groups + other_groups


def _build_search_tree(
    repository: EntryRepository,
    *,
    access: UserAccess,
    parent_path: str,
    current_path: str,
    expanded_paths: set[str],
    visible_directory_paths: set[str],
    matched_directory_paths: set[str],
    result_files_by_parent: dict[str, list[Entry]],
    result_ids: set[int],
    selected_id: int | None,
) -> list[SearchTreeNode]:
    nodes: list[SearchTreeNode] = []
    for entry in repository.list_child_directories(
        access.nas_id,
        parent_path,
        allowed_share_paths=access.share_paths,
    ):
        if entry.full_path not in visible_directory_paths:
            continue
        should_expand = entry.full_path in expanded_paths
        children: list[SearchTreeNode] = []
        if should_expand:
            children.extend(
                _build_search_tree(
                    repository,
                    access=access,
                    parent_path=entry.full_path,
                    current_path=current_path,
                    expanded_paths=expanded_paths,
                    visible_directory_paths=(
                        visible_directory_paths
                    ),
                    matched_directory_paths=(
                        matched_directory_paths
                    ),
                    result_files_by_parent=(
                        result_files_by_parent
                    ),
                    result_ids=result_ids,
                    selected_id=selected_id,
                )
            )
            for file_entry in result_files_by_parent.get(
                entry.full_path,
                [],
            ):
                children.append(
                    SearchTreeNode(
                        entry=file_entry,
                        children=[],
                        is_current=False,
                        is_ancestor=False,
                        is_match=True,
                        is_selected=(
                            file_entry.id == selected_id
                        ),
                        is_result=True,
                    )
                )

        is_current = entry.full_path == current_path
        nodes.append(
            SearchTreeNode(
                entry=entry,
                children=children,
                is_current=is_current,
                is_ancestor=(
                    should_expand and not is_current
                ),
                is_match=(
                    entry.full_path
                    in matched_directory_paths
                ),
                is_selected=entry.id == selected_id,
                is_result=entry.id in result_ids,
            )
        )
    return nodes


def _tree_context(
    repository: EntryRepository,
    items: list[Entry],
    selected_result: Entry | None,
    access: UserAccess,
) -> tuple[list[SearchTreeNode], str]:
    if selected_result is None:
        return [], "/"

    expanded_paths: set[str] = set()
    visible_directory_paths: set[str] = set()
    matched_directory_paths: set[str] = set()
    result_files_by_parent: dict[str, list[Entry]] = (
        defaultdict(list)
    )
    result_ids = {item.id for item in items}

    for item in items:
        focus_path = _focus_directory_path(item)
        visible_directory_paths.update(
            _expanded_paths(focus_path)
        )
        if item.entry_type == "directory":
            matched_directory_paths.add(
                item.full_path
            )
        else:
            matched_directory_paths.add(
                item.parent_path
            )
            result_files_by_parent[
                item.parent_path
            ].append(item)

    current_path = _focus_directory_path(
        selected_result
    )
    expanded_paths = set(visible_directory_paths)
    expanded_paths.update(
        _expanded_paths(current_path)
    )

    tree_nodes = _build_search_tree(
        repository,
        access=access,
        parent_path="/",
        current_path=current_path,
        expanded_paths=expanded_paths,
        visible_directory_paths=(
            visible_directory_paths
        ),
        matched_directory_paths=(
            matched_directory_paths
        ),
        result_files_by_parent=result_files_by_parent,
        result_ids=result_ids,
        selected_id=selected_result.id,
    )
    return tree_nodes, current_path


def _summary_context(
    query: str,
    results: Page[Entry],
    result_groups: list[SearchResultGroup],
) -> SearchSummaryContext:
    directories: list[SearchSummaryDirectory] = []
    for group in result_groups:
        directories.append(
            SearchSummaryDirectory(
                path=group.path,
                item_count=len(group.items),
                items=tuple(
                    SearchSummaryItem(
                        name=item.name,
                        full_path=item.full_path,
                        entry_type=item.entry_type,
                    )
                    for item in group.items
                ),
            )
        )
    return SearchSummaryContext(
        query=query,
        total=results.total,
        page=results.page,
        page_size=results.page_size,
        directories=tuple(directories),
    )


def _browse_url(path: str) -> str:
    return f"/browse?path={quote(_normalize_path(path), safe='/')}"


def _parent_directory_path(path: str) -> str:
    normalized = _normalize_path(path)
    if normalized == "/":
        return "/"
    parent = normalized.rsplit("/", 1)[0]
    return parent or "/"


def _summary_answer_links(
    context: SearchSummaryContext,
) -> list[dict[str, str]]:
    links: dict[str, str] = {}
    for directory in context.directories:
        directory_path = _normalize_path(directory.path)
        links[directory_path] = _browse_url(directory_path)
        for item in directory.items:
            item_path = _normalize_path(item.full_path)
            if item.entry_type == "directory":
                links[item_path] = _browse_url(item_path)
            else:
                parent_path = _parent_directory_path(
                    item_path
                )
                links[item_path] = _browse_url(parent_path)
                links[parent_path] = _browse_url(parent_path)

    return [
        {
            "path": path,
            "url": links[path],
        }
        for path in sorted(
            links,
            key=lambda value: (-len(value), value),
        )
    ]


@router.get(
    "",
    response_class=HTMLResponse,
)
def search(
    request: Request,
    q: str = Query(""),
    selected: int | None = Query(None),
    session: Session = Depends(get_session),
):
    access = current_access(request)
    if access is None:
        return RedirectResponse(
            "/access",
            status_code=303,
        )

    query = q.strip()
    repository = EntryRepository(session)
    results = repository.search_all(
        query,
        nas_id=access.nas_id,
        allowed_share_paths=access.share_paths,
    )
    result_start = (
        1 if results.total else 0
    )
    result_end = results.total
    total_pages = (
        1 if results.total else 0
    )
    selected_result = _select_result(
        results.items,
        selected,
    )
    result_groups = _group_results(
        results.items,
        selected_result.id
        if selected_result is not None
        else None,
    )
    selected_group = next(
        (
            group
            for group in result_groups
            if group.is_selected
        ),
        None,
    )
    tree_nodes, current_path = _tree_context(
        repository,
        results.items,
        selected_result,
        access,
    )
    summary_payload = None
    if results.total:
        summary_payload = sign_search_summary_payload(
            _summary_context(
                query,
                results,
                result_groups,
            ),
            nas_id=access.nas_id,
            share_paths=access.share_paths,
            secret=(
                request.app.state.search_summary_payload_secret
            ),
        )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="search.html",
        context={
            "query": query,
            "results": results,
            "selected": (
                selected_result.id
                if selected_result is not None
                else None
            ),
            "selected_result": selected_result,
            "selected_result_breadcrumbs": (
                _breadcrumb_parts(
                    selected_result.full_path
                )
                if selected_result is not None
                else []
            ),
            "selected_group_size": (
                len(selected_group.items)
                if selected_group is not None
                else 0
            ),
            "current_path": current_path,
            "current_path_breadcrumbs": (
                _breadcrumb_parts(current_path)
                if selected_result is not None
                else []
            ),
            "result_groups": result_groups,
            "tree_nodes": tree_nodes,
            "result_start": result_start,
            "result_end": result_end,
            "total_pages": total_pages,
            "summary_payload": summary_payload,
            "breadcrumb_parts": _breadcrumb_parts,
            "highlight_match": _build_highlighter(
                query
            ),
            "format_size": _format_size,
            "format_modified": _format_modified,
        },
    )


@router.post("/summary")
async def summarize_search_results(
    request: Request,
    payload: SearchSummaryRequest,
) -> dict[str, object]:
    access = current_access(request)
    if access is None:
        raise HTTPException(
            status_code=401,
            detail="请先登录",
        )

    question = payload.question.strip()
    if not question:
        raise HTTPException(
            status_code=422,
            detail="请输入问题",
        )

    try:
        payload_access, context = load_search_summary_payload(
            payload.payload,
            payload.signature,
            secret=(
                request.app.state.search_summary_payload_secret
            ),
        )
    except SearchSummaryPayloadError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    if payload_access.nas_id != access.nas_id or (
        payload_access.share_paths
        != tuple(sorted(access.share_paths))
    ):
        raise HTTPException(
            status_code=403,
            detail="当前账号无权使用这份总结数据",
        )

    if not context.query.strip():
        raise HTTPException(
            status_code=422,
            detail="请输入关键词",
        )

    if not context.directories:
        return {
            "answer": "当前搜索没有可参考的结果。",
            "links": [],
        }

    summarizer = getattr(
        request.app.state,
        "search_summarizer",
        None,
    )
    if summarizer is None:
        raise HTTPException(
            status_code=503,
            detail="管理员未配置 AI 总结",
        )
    try:
        answer = await summarizer.answer(
            context,
            question,
        )
    except SearchSummaryUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc
    return {
        "answer": answer,
        "links": _summary_answer_links(context),
    }
