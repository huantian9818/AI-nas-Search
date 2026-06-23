from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.nas import NasRepository
from nas_index.web.routes.access import current_access

router = APIRouter(prefix="/thumbnails")


@router.get("/{entry_id}")
async def thumbnail(
    entry_id: int,
    request: Request,
):
    access = current_access(request)
    if access is None:
        raise HTTPException(status_code=404)

    with request.app.state.session_factory() as session:
        entry = EntryRepository(session).get_by_id(entry_id)
        if (
            entry is None
            or entry.nas_id != access.nas_id
            or entry.share_path not in access.share_paths
        ):
            raise HTTPException(status_code=404)

        connection = NasRepository(session).connection_for_indexer(
            entry.nas_id,
        )
        if connection is None:
            raise HTTPException(status_code=503)

        session.expunge(entry)

    result = await request.app.state.thumbnail_service.get(
        entry,
        connection,
    )
    if result is None:
        raise HTTPException(status_code=404)

    return FileResponse(
        result.path,
        media_type=result.media_type,
    )
