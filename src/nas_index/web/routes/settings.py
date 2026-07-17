from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from nas_index.config import AppSettings
from nas_index.qnap.client import QnapProbeResult, probe_qnap_connection
from nas_index.qnap.errors import QnapError
from nas_index.repositories.config import ConfigRepository
from nas_index.repositories.nas import NasRepository
from nas_index.types import NasConnection
from nas_index.web.dependencies import get_session
from nas_index.web.routes.admin import admin_login_redirect

router = APIRouter()

CONNECTION_TEST_REQUIRED = (
    "请先使用当前连接信息测试成功后再保存"
)


def normalize_base_url(
    host: str,
    use_https: bool,
) -> str:
    hostname = normalize_host(host)
    protocol = "https" if use_https else "http"
    return f"{protocol}://{hostname}"


def normalize_host(host: str) -> str:
    host = host.strip().rstrip("/")
    parsed = urlsplit(
        host
        if "://" in host
        else f"//{host}"
    )
    return parsed.hostname or host


async def test_connection(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    skip_tls_verify: bool,
    settings: AppSettings,
) -> QnapProbeResult:
    return await probe_qnap_connection(
        host=host,
        port=port,
        username=username,
        password=password,
        skip_tls_verify=skip_tls_verify,
        timeout_seconds=settings.qnap_timeout_seconds,
        retry_attempts=settings.qnap_retry_attempts,
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


def _connection_from_form(
    repository: NasRepository,
    *,
    nas_id: int | None,
    host: str,
    port: int,
    use_https: bool,
    skip_tls_verify: bool,
    username: str,
    password: str,
) -> NasConnection:
    resolved_password = resolve_password(
        repository,
        nas_id=nas_id,
        password=password,
    )
    if not resolved_password:
        raise ValueError("首次保存时必须输入索引账号密码")
    return NasConnection(
        base_url=normalize_base_url(host, use_https),
        port=port,
        use_https=use_https,
        username=username.strip(),
        password=resolved_password,
        skip_tls_verify=skip_tls_verify,
    )


def resolve_password(
    repository: NasRepository,
    *,
    nas_id: int | None,
    password: str,
) -> str:
    resolved_password = password
    if not resolved_password and nas_id is not None:
        credential = repository.get_credential(nas_id)
        if credential is not None:
            resolved_password = credential.password
    return resolved_password


def _settings_error(
    request: Request,
    repository: NasRepository,
    message: str,
    *,
    status_code: int = 422,
):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=_settings_context(
            repository,
            error=message,
        ),
        status_code=status_code,
    )


@router.get(
    "/settings",
    response_class=HTMLResponse,
)
def settings_page(
    request: Request,
    session: Session = Depends(get_session),
):
    redirect = admin_login_redirect(request)
    if redirect is not None:
        return redirect

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
    skip_tls_verify: bool = Form(False),
    enabled: bool = Form(False),
    sync_interval_minutes: int = Form(..., ge=1),
    username: str = Form(...),
    password: str = Form(""),
    connection_test_token: str = Form(""),
    session: Session = Depends(get_session),
):
    redirect = admin_login_redirect(request, next_path="/settings")
    if redirect is not None:
        return redirect

    repository = NasRepository(session)
    try:
        if not resolve_password(
            repository,
            nas_id=None,
            password=password,
        ):
            raise ValueError("首次保存时必须输入索引账号密码")
        tested_connection = request.app.state.connection_test_store.get(
            connection_test_token
        )
        if tested_connection is None:
            return _settings_error(
                request,
                repository,
                CONNECTION_TEST_REQUIRED,
            )
        connection = _connection_from_form(
            repository,
            nas_id=None,
            host=host,
            port=port,
            use_https=tested_connection.use_https,
            skip_tls_verify=skip_tls_verify,
            username=username,
            password=password,
        )
        if not request.app.state.connection_test_store.matches(
            connection_test_token,
            connection,
        ):
            return _settings_error(
                request,
                repository,
                CONNECTION_TEST_REQUIRED,
            )
        repository.create_server(
            name=name,
            base_url=connection.base_url,
            port=port,
            use_https=connection.use_https,
            skip_tls_verify=connection.skip_tls_verify,
            enabled=enabled,
            sync_interval_minutes=sync_interval_minutes,
            username=username,
            password=connection.password,
        )
    except ValueError as exc:
        return _settings_error(
            request,
            repository,
            str(exc),
        )
    session.commit()
    request.app.state.connection_test_store.delete(
        connection_test_token
    )
    return RedirectResponse(
        "/settings",
        status_code=303,
    )


@router.post(
    "/settings/nas/test",
    response_class=HTMLResponse,
)
async def test_nas_form_connection(
    request: Request,
    nas_id: int | None = Form(None),
    host: str = Form(...),
    port: int = Form(..., ge=1, le=65535),
    skip_tls_verify: bool = Form(False),
    username: str = Form(...),
    password: str = Form(""),
    session: Session = Depends(get_session),
):
    redirect = admin_login_redirect(request, next_path="/settings")
    if redirect is not None:
        return redirect

    repository = NasRepository(session)
    try:
        resolved_password = resolve_password(
            repository,
            nas_id=nas_id,
            password=password,
        )
        if not resolved_password:
            raise ValueError("首次保存时必须输入索引账号密码")
        probe = await test_connection(
            host=normalize_host(host),
            port=port,
            username=username.strip(),
            password=resolved_password,
            skip_tls_verify=skip_tls_verify,
            settings=request.app.state.settings,
        )
        token = request.app.state.connection_test_store.create(
            probe.connection
        )
        context = {
            "success": True,
            "message": (
                "连接成功"
                f"（{'HTTPS' if probe.connection.use_https else 'HTTP'}），"
                f"可访问 {probe.share_count} 个共享目录"
            ),
            "connection_test_token": token,
        }
    except QnapError as exc:
        context = {
            "success": False,
            "message": str(exc),
        }
    except ValueError as exc:
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

@router.post("/settings/nas/{nas_id}")
def update_nas(
    request: Request,
    nas_id: int,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(..., ge=1, le=65535),
    skip_tls_verify: bool = Form(False),
    enabled: bool = Form(False),
    sync_interval_minutes: int = Form(..., ge=1),
    username: str = Form(...),
    password: str = Form(""),
    connection_test_token: str = Form(""),
    session: Session = Depends(get_session),
):
    redirect = admin_login_redirect(request, next_path="/settings")
    if redirect is not None:
        return redirect

    repository = NasRepository(session)
    try:
        tested_connection = request.app.state.connection_test_store.get(
            connection_test_token
        )
        if tested_connection is None:
            return _settings_error(
                request,
                repository,
                CONNECTION_TEST_REQUIRED,
            )
        connection = _connection_from_form(
            repository,
            nas_id=nas_id,
            host=host,
            port=port,
            use_https=tested_connection.use_https,
            skip_tls_verify=skip_tls_verify,
            username=username,
            password=password,
        )
        if not request.app.state.connection_test_store.matches(
            connection_test_token,
            connection,
        ):
            return _settings_error(
                request,
                repository,
                CONNECTION_TEST_REQUIRED,
            )
        repository.update_server(
            nas_id,
            name=name,
            base_url=connection.base_url,
            port=port,
            use_https=connection.use_https,
            skip_tls_verify=connection.skip_tls_verify,
            enabled=enabled,
            sync_interval_minutes=sync_interval_minutes,
            username=username,
            password=connection.password,
        )
    except (LookupError, ValueError) as exc:
        return _settings_error(
            request,
            repository,
            str(exc),
        )
    session.commit()
    request.app.state.connection_test_store.delete(
        connection_test_token
    )
    return RedirectResponse(
        "/settings",
        status_code=303,
    )


@router.post("/settings")
async def save_settings(
    request: Request,
    host: str = Form(...),
    port: int = Form(..., ge=1, le=65535),
    use_https: bool = Form(False),
    username: str = Form(...),
    password: str = Form(""),
    session: Session = Depends(get_session),
):
    redirect = admin_login_redirect(request, next_path="/settings")
    if redirect is not None:
        return redirect

    repository = ConfigRepository(session)
    try:
        probe = await test_connection(
            host=normalize_host(host),
            port=port,
            username=username.strip(),
            password=password,
            skip_tls_verify=False,
            settings=request.app.state.settings,
        )
        repository.save(probe.connection)
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
    redirect = admin_login_redirect(request, next_path="/settings")
    if redirect is not None:
        return redirect

    config = ConfigRepository(session).get()
    if config is None:
        context = {
            "success": False,
            "message": "请先保存 NAS 设置",
        }
    else:
        try:
            probe = await test_connection(
                host=normalize_host(config.base_url),
                port=config.port,
                username=config.username,
                password=config.password,
                skip_tls_verify=config.skip_tls_verify,
                settings=request.app.state.settings,
            )
            context = {
                "success": True,
                "message": (
                    "连接成功"
                    f"（{'HTTPS' if probe.connection.use_https else 'HTTP'}），"
                    f"可访问 {probe.share_count} 个共享目录"
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
