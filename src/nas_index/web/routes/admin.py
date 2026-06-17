from secrets import compare_digest
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/admin")

ADMIN_COOKIE_NAME = "nas_admin"


def _safe_next(value: str | None) -> str:
    if not value:
        return "/"
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def current_admin(request: Request) -> bool:
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    return request.app.state.admin_store.get(token)


def admin_login_redirect(
    request: Request,
    *,
    next_path: str | None = None,
) -> RedirectResponse | None:
    if current_admin(request):
        return None
    target = _safe_next(next_path or request.url.path)
    return RedirectResponse(
        f"/admin/login?next={quote(target)}",
        status_code=303,
    )


@router.get(
    "/login",
    response_class=HTMLResponse,
)
def login_page(
    request: Request,
    next: str = "/",
):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={
            "next": _safe_next(next),
            "error": None,
            "configured": (
                request.app.state.settings.admin_password
                is not None
            ),
        },
    )


@router.post(
    "/login",
    response_class=HTMLResponse,
)
def login(
    request: Request,
    password: str = Form(...),
    next: str = Form("/"),
):
    configured_password = request.app.state.settings.admin_password
    safe_next = _safe_next(next)
    if configured_password is None:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={
                "next": safe_next,
                "error": "未配置管理员密码",
                "configured": False,
            },
            status_code=503,
        )
    if not compare_digest(password, configured_password):
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={
                "next": safe_next,
                "error": "管理员密码错误",
                "configured": True,
            },
            status_code=401,
        )

    response = RedirectResponse(
        safe_next,
        status_code=303,
    )
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        request.app.state.admin_store.create(),
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
def logout(request: Request):
    request.app.state.admin_store.delete(
        request.cookies.get(ADMIN_COOKIE_NAME)
    )
    response = RedirectResponse(
        "/access",
        status_code=303,
    )
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return response
