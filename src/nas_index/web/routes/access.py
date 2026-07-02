from dataclasses import dataclass
from urllib.parse import quote

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
ACCESS_ERROR_MESSAGES = {
    "sid_expired": "NAS 登录已过期，请重新登录",
}


def _safe_next(value: str | None) -> str:
    if not value:
        return "/browse"
    if not value.startswith("/") or value.startswith("//"):
        return "/browse"
    return value


def access_login_redirect(request: Request) -> RedirectResponse:
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(
        f"/access?next={quote(target, safe='')}",
        status_code=303,
    )


@dataclass(frozen=True)
class AccessCheckResult:
    share_paths: tuple[str, ...]
    qnap_sid: str | None


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
) -> AccessCheckResult:
    connection = server.to_connection(
        username=username,
        password=password,
    )
    client = QnapClient(
        connection,
        timeout_seconds=settings.qnap_timeout_seconds,
        retry_attempts=settings.qnap_retry_attempts,
    )
    try:
        await client.login()
        shares = await client.list_shares()
        return AccessCheckResult(
            share_paths=tuple(
                sorted({share.full_path for share in shares})
            ),
            qnap_sid=client.sid,
        )
    finally:
        await client.close()


def _normalize_access_check_result(
    result,
) -> AccessCheckResult:
    if isinstance(result, AccessCheckResult):
        return result
    return AccessCheckResult(
        share_paths=tuple(result),
        qnap_sid=None,
    )


@router.get(
    "/access",
    response_class=HTMLResponse,
    name="access",
)
def access_page(
    request: Request,
    next: str = "/browse",
    reason: str | None = None,
    session: Session = Depends(get_session),
):
    servers = NasRepository(session).list_enabled_servers()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="access.html",
        context={
            "servers": servers,
            "access": current_access(request),
            "error": ACCESS_ERROR_MESSAGES.get(reason),
            "next": _safe_next(next),
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
    next: str = Form("/browse"),
    session: Session = Depends(get_session),
):
    safe_next = _safe_next(next)
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
                "next": safe_next,
            },
            status_code=422,
        )

    checker = getattr(
        request.app.state,
        "access_checker",
        check_user_access,
    )
    try:
        check_result = _normalize_access_check_result(
            await checker(
                server=server,
                username=username,
                password=password,
                settings=request.app.state.settings,
            )
        )
    except QnapError as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="access.html",
            context={
                "servers": servers,
                "access": current_access(request),
                "error": str(exc),
                "next": safe_next,
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
                "next": safe_next,
            },
            status_code=502,
        )

    token = request.app.state.access_store.create(
        nas_id=nas_id,
        username=username,
        share_paths=check_result.share_paths,
        qnap_sid=check_result.qnap_sid,
    )
    response = RedirectResponse(
        safe_next,
        status_code=303,
    )
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        token,
        max_age=request.app.state.settings.user_access_ttl_seconds,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/access/logout")
def logout_current_user(request: Request):
    request.app.state.access_store.delete(
        request.cookies.get(ACCESS_COOKIE_NAME)
    )
    response = RedirectResponse(
        "/",
        status_code=303,
    )
    response.delete_cookie(ACCESS_COOKIE_NAME)
    return response
