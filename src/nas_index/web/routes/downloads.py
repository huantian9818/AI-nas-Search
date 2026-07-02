from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from nas_index.config import AppSettings
from nas_index.models import Entry
from nas_index.qnap.client import (
    QnapClient,
    build_batch_download_url,
    build_download_url,
)
from nas_index.qnap.errors import QnapError, QnapSessionExpired
from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.nas import NasRepository
from nas_index.types import NasServerValue, UserAccess
from nas_index.web.dependencies import get_session
from nas_index.web.routes.access import (
    ACCESS_COOKIE_NAME,
    current_access,
)

router = APIRouter(prefix="/downloads")


@router.post("/batch")
async def download_batch(
    request: Request,
    entry_ids: list[int] = Form(...),
    session: Session = Depends(get_session),
):
    access = current_access(request)
    if access is None or access.qnap_sid is None:
        return _redirect_to_access(request)
    if not entry_ids:
        raise HTTPException(status_code=422)

    repository = EntryRepository(session)
    entries = [
        _allowed_download_entry(
            repository,
            entry_id,
            access.nas_id,
            access.share_paths,
        )
        for entry_id in entry_ids
    ]
    parent_paths = {entry.parent_path for entry in entries}
    if len(parent_paths) != 1:
        raise HTTPException(
            status_code=422,
            detail="只能批量下载同一目录下的文件",
        )

    server = NasRepository(session).get_server(access.nas_id)
    if server is None:
        raise HTTPException(status_code=404)
    redirect = await _validate_download_access(
        request,
        server=server,
        access=access,
    )
    if redirect is not None:
        return redirect

    return RedirectResponse(
        build_batch_download_url(
            endpoint=f"{server.base_url.rstrip('/')}:{server.port}",
            sid=access.qnap_sid,
            parent_path=entries[0].parent_path,
            source_files=tuple(entry.name for entry in entries),
        ),
        status_code=303,
    )


@router.get("/{entry_id}")
async def download_entry(
    entry_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    access = current_access(request)
    if access is None or access.qnap_sid is None:
        return _redirect_to_access(request)

    entry = EntryRepository(session).get_by_id(entry_id)
    if (
        entry is None
        or entry.nas_id != access.nas_id
        or entry.share_path not in access.share_paths
        or entry.name.startswith(".")
    ):
        raise HTTPException(status_code=404)

    server = NasRepository(session).get_server(access.nas_id)
    if server is None:
        raise HTTPException(status_code=404)
    redirect = await _validate_download_access(
        request,
        server=server,
        access=access,
    )
    if redirect is not None:
        return redirect

    return RedirectResponse(
        build_download_url(
            endpoint=f"{server.base_url.rstrip('/')}:{server.port}",
            sid=access.qnap_sid,
            full_path=entry.full_path,
            is_directory=entry.entry_type == "directory",
        ),
        status_code=303,
    )


async def check_download_access(
    *,
    server: NasServerValue,
    access: UserAccess,
    settings: AppSettings,
) -> None:
    client = QnapClient(
        server.to_connection(
            username=access.username,
            password="",
        ),
        timeout_seconds=settings.qnap_timeout_seconds,
        retry_attempts=settings.qnap_retry_attempts,
    )
    client.sid = access.qnap_sid
    try:
        await client.validate_sid()
    finally:
        await client.close()


async def _validate_download_access(
    request: Request,
    *,
    server: NasServerValue,
    access: UserAccess,
) -> RedirectResponse | None:
    checker = getattr(
        request.app.state,
        "download_access_checker",
        check_download_access,
    )
    try:
        await checker(
            server=server,
            access=access,
            settings=request.app.state.settings,
        )
    except QnapSessionExpired:
        return _redirect_to_access(
            request,
            reason="sid_expired",
            clear_session=True,
        )
    except QnapError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc
    return None


def _redirect_to_access(
    request: Request,
    *,
    reason: str | None = None,
    clear_session: bool = False,
) -> RedirectResponse:
    next_target = _download_next_target(request)
    location = (
        f"/access?next={quote(next_target, safe='')}"
    )
    if reason:
        location = f"{location}&reason={quote(reason, safe='')}"
    response = RedirectResponse(
        location,
        status_code=303,
    )
    if clear_session:
        request.app.state.access_store.delete(
            request.cookies.get(ACCESS_COOKIE_NAME)
        )
        response.delete_cookie(ACCESS_COOKIE_NAME)
    return response


def _download_next_target(request: Request) -> str:
    referer = request.headers.get("referer")
    if referer:
        referer_url = urlsplit(referer)
        current_base = urlsplit(str(request.base_url))
        if (
            referer_url.scheme == current_base.scheme
            and referer_url.netloc == current_base.netloc
            and referer_url.path.startswith("/")
            and not referer_url.path.startswith("//")
        ):
            target = referer_url.path
            if referer_url.query:
                target = f"{target}?{referer_url.query}"
            return target
    if request.method == "GET":
        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        if target.startswith("/") and not target.startswith("//"):
            return target
    return "/browse"


def _allowed_download_entry(
    repository: EntryRepository,
    entry_id: int,
    nas_id: int,
    share_paths: tuple[str, ...],
) -> Entry:
    entry = repository.get_by_id(entry_id)
    if (
        entry is None
        or entry.nas_id != nas_id
        or entry.share_path not in share_paths
        or entry.entry_type != "file"
        or entry.name.startswith(".")
    ):
        raise HTTPException(status_code=404)
    return entry
