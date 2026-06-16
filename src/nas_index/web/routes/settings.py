from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from nas_index.qnap.client import QnapClient
from nas_index.qnap.errors import QnapError
from nas_index.repositories.config import ConfigRepository
from nas_index.repositories.nas import NasRepository
from nas_index.types import NasConnection
from nas_index.web.dependencies import get_session

router = APIRouter()


def normalize_base_url(
    host: str,
    use_https: bool,
) -> str:
    host = host.strip().rstrip("/")
    parsed = urlsplit(
        host
        if "://" in host
        else f"//{host}"
    )
    hostname = parsed.hostname or host
    protocol = "https" if use_https else "http"
    return f"{protocol}://{hostname}"


async def test_connection(
    connection: NasConnection,
) -> int:
    async with QnapClient(connection) as client:
        return len(
            await client.list_shares()
        )


def _settings_context(
    repository: NasRepository,
    **extra: object,
) -> dict[str, object]:
    servers = repository.list_servers()
    credential_usernames = {}
    for server in servers:
        credential = repository.get_credential(server.id)
        credential_usernames[server.id] = (
            credential.username if credential else ""
        )
    return {
        "servers": servers,
        "credential_usernames": credential_usernames,
        **extra,
    }


@router.get(
    "/settings",
    response_class=HTMLResponse,
)
def settings_page(
    request: Request,
    session: Session = Depends(get_session),
):
    repository = NasRepository(session)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=_settings_context(repository),
    )


@router.post("/settings/nas")
def create_nas(
    request: Request,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(..., ge=1, le=65535),
    use_https: bool = Form(False),
    enabled: bool = Form(False),
    sync_interval_minutes: int = Form(..., ge=1),
    full_resync_interval_hours: int = Form(..., ge=1),
    username: str = Form(...),
    password: str = Form(""),
    session: Session = Depends(get_session),
):
    repository = NasRepository(session)
    try:
        repository.create_server(
            name=name,
            base_url=normalize_base_url(
                host,
                use_https,
            ),
            port=port,
            use_https=use_https,
            enabled=enabled,
            sync_interval_minutes=sync_interval_minutes,
            full_resync_interval_hours=(
                full_resync_interval_hours
            ),
            username=username,
            password=password,
        )
    except ValueError as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="settings.html",
            context=_settings_context(
                repository,
                error=str(exc),
            ),
            status_code=422,
        )
    session.commit()
    return RedirectResponse(
        "/settings",
        status_code=303,
    )


@router.post("/settings/nas/{nas_id}")
def update_nas(
    request: Request,
    nas_id: int,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(..., ge=1, le=65535),
    use_https: bool = Form(False),
    enabled: bool = Form(False),
    sync_interval_minutes: int = Form(..., ge=1),
    full_resync_interval_hours: int = Form(..., ge=1),
    username: str = Form(...),
    password: str = Form(""),
    session: Session = Depends(get_session),
):
    repository = NasRepository(session)
    try:
        repository.update_server(
            nas_id,
            name=name,
            base_url=normalize_base_url(
                host,
                use_https,
            ),
            port=port,
            use_https=use_https,
            enabled=enabled,
            sync_interval_minutes=sync_interval_minutes,
            full_resync_interval_hours=(
                full_resync_interval_hours
            ),
            username=username,
            password=password,
        )
    except (LookupError, ValueError) as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="settings.html",
            context=_settings_context(
                repository,
                error=str(exc),
            ),
            status_code=422,
        )
    session.commit()
    return RedirectResponse(
        "/settings",
        status_code=303,
    )


@router.post("/settings")
def save_settings(
    request: Request,
    host: str = Form(...),
    port: int = Form(..., ge=1, le=65535),
    use_https: bool = Form(False),
    username: str = Form(...),
    password: str = Form(""),
    session: Session = Depends(get_session),
):
    repository = ConfigRepository(session)
    try:
        repository.save(
            NasConnection(
                normalize_base_url(
                    host,
                    use_https,
                ),
                port,
                use_https,
                username,
                password,
            )
        )
    except ValueError as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="settings.html",
            context=_settings_context(
                NasRepository(session),
                error=str(exc),
            ),
            status_code=422,
        )
    session.commit()
    return RedirectResponse(
        "/settings",
        status_code=303,
    )


@router.post(
    "/settings/test",
    response_class=HTMLResponse,
)
async def connection_test(
    request: Request,
    session: Session = Depends(get_session),
):
    config = ConfigRepository(session).get()
    if config is None:
        context = {
            "success": False,
            "message": "请先保存 NAS 设置",
        }
    else:
        try:
            share_count = await test_connection(
                config
            )
            context = {
                "success": True,
                "message": (
                    "连接成功，可访问 "
                    f"{share_count} 个共享目录"
                ),
            }
        except QnapError as exc:
            context = {
                "success": False,
                "message": str(exc),
            }
        except Exception:
            context = {
                "success": False,
                "message": "连接测试失败",
            }
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="partials/connection_result.html",
        context=context,
    )


@router.post(
    "/settings/nas/{nas_id}/test",
    response_class=HTMLResponse,
)
async def nas_connection_test(
    request: Request,
    nas_id: int,
    session: Session = Depends(get_session),
):
    config = NasRepository(session).connection_for_indexer(
        nas_id
    )
    if config is None:
        context = {
            "success": False,
            "message": "请先保存 NAS 设置",
        }
    else:
        try:
            share_count = await test_connection(
                config
            )
            context = {
                "success": True,
                "message": (
                    "连接成功，可访问 "
                    f"{share_count} 个共享目录"
                ),
            }
        except QnapError as exc:
            context = {
                "success": False,
                "message": str(exc),
            }
        except Exception:
            context = {
                "success": False,
                "message": "连接测试失败",
            }
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="partials/connection_result.html",
        context=context,
    )
