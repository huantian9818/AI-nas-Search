from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from nas_index.config import AppSettings
from nas_index.qnap.client import QnapClient
from nas_index.qnap.errors import QnapError
from nas_index.repositories.nas import NasRepository
from nas_index.types import NasServerValue, UserAccess
from nas_index.web.dependencies import get_session

router = APIRouter()

ACCESS_COOKIE_NAME = "nas_access"


def current_access(
    request: Request,
) -> UserAccess | None:
    token = request.cookies.get(ACCESS_COOKIE_NAME)
    return request.app.state.access_store.get(token)


async def check_user_access(
    *,
    server: NasServerValue,
    username: str,
    password: str,
    settings: AppSettings,
) -> tuple[str, ...]:
    connection = server.to_connection(
        username=username,
        password=password,
    )
    async with QnapClient(
        connection,
        timeout_seconds=settings.qnap_timeout_seconds,
        retry_attempts=settings.qnap_retry_attempts,
    ) as client:
        shares = await client.list_shares()
    return tuple(
        sorted({share.full_path for share in shares})
    )


@router.get(
    "/access",
    response_class=HTMLResponse,
    name="access",
)
def access_page(
    request: Request,
    session: Session = Depends(get_session),
):
    servers = NasRepository(session).list_enabled_servers()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="access.html",
        context={
            "servers": servers,
            "access": current_access(request),
            "error": None,
        },
    )


@router.post(
    "/access",
    response_class=HTMLResponse,
)
async def create_access(
    request: Request,
    nas_id: int = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    repository = NasRepository(session)
    server = repository.get_server(nas_id)
    servers = repository.list_enabled_servers()
    if server is None:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="access.html",
            context={
                "servers": servers,
                "access": current_access(request),
                "error": "NAS 不存在",
            },
            status_code=422,
        )

    checker = getattr(
        request.app.state,
        "access_checker",
        check_user_access,
    )
    try:
        share_paths = await checker(
            server=server,
            username=username,
            password=password,
            settings=request.app.state.settings,
        )
    except QnapError as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="access.html",
            context={
                "servers": servers,
                "access": current_access(request),
                "error": str(exc),
            },
            status_code=401,
        )
    except Exception:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="access.html",
            context={
                "servers": servers,
                "access": current_access(request),
                "error": "访问验证失败",
            },
            status_code=502,
        )

    token = request.app.state.access_store.create(
        nas_id=nas_id,
        username=username,
        share_paths=tuple(share_paths),
    )
    response = RedirectResponse(
        "/browse",
        status_code=303,
    )
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
    )
    return response
