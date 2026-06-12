from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from nas_index.qnap.client import QnapClient
from nas_index.qnap.errors import QnapError
from nas_index.repositories.config import ConfigRepository
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


@router.get(
    "/settings",
    response_class=HTMLResponse,
)
def settings_page(
    request: Request,
    session: Session = Depends(get_session),
):
    config = ConfigRepository(session).get()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"config": config},
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
            context={
                "config": repository.get(),
                "error": str(exc),
            },
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
