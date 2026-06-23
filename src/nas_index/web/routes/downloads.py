from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from nas_index.models import Entry
from nas_index.qnap.client import build_batch_download_url
from nas_index.qnap.client import build_download_url
from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.nas import NasRepository
from nas_index.web.dependencies import get_session
from nas_index.web.routes.access import current_access

router = APIRouter(prefix="/downloads")


@router.post("/batch")
def download_batch(
    request: Request,
    entry_ids: list[int] = Form(...),
    session: Session = Depends(get_session),
):
    access = current_access(request)
    if access is None or access.qnap_sid is None:
        return RedirectResponse(
            "/access",
            status_code=303,
        )
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
def download_entry(
    entry_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    access = current_access(request)
    if access is None or access.qnap_sid is None:
        return RedirectResponse(
            "/access",
            status_code=303,
        )

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

    return RedirectResponse(
        build_download_url(
            endpoint=f"{server.base_url.rstrip('/')}:{server.port}",
            sid=access.qnap_sid,
            full_path=entry.full_path,
            is_directory=entry.entry_type == "directory",
        ),
        status_code=303,
    )


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
