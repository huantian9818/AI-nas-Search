import base64

import httpx
import pytest

from nas_index.qnap.client import (
    QnapClient,
    QnapProbeResult,
    probe_qnap_connection,
)
from nas_index.qnap.errors import (
    QnapAuthenticationError,
    QnapTlsVerificationError,
    QnapTwoStepRequired,
)
from nas_index.types import NasConnection


CONNECTION = NasConnection(
    "http://nas.local",
    8080,
    False,
    "indexer",
    "päss",
)


@pytest.mark.asyncio
async def test_login_base64_encodes_password_and_logout_uses_sid():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("authLogin.cgi"):
            return httpx.Response(
                200,
                text=(
                    "<QDocRoot><authPassed>1</authPassed>"
                    "<authSid>abc123</authSid></QDocRoot>"
                ),
            )
        return httpx.Response(
            200,
            text="<QDocRoot><authPassed>0</authPassed></QDocRoot>",
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http:
        client = QnapClient(CONNECTION, http=http)
        await client.login()
        await client.logout()

    expected = base64.b64encode("päss".encode()).decode()
    assert requests[0].url.params["pwd"] == expected
    assert requests[0].url.params["remme"] == "0"
    assert requests[1].url.params["sid"] == "abc123"
    assert "päss" not in str(requests[0].url)


@pytest.mark.asyncio
async def test_login_rejects_two_step_account():
    response = (
        "<QDocRoot><authPassed>0</authPassed>"
        "<need_2sv>1</need_2sv></QDocRoot>"
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, text=response)
    )

    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(QnapTwoStepRequired, match="两步验证"):
            await QnapClient(CONNECTION, http=http).login()


@pytest.mark.asyncio
async def test_login_maps_invalid_credentials():
    response = (
        "<QDocRoot><authPassed>0</authPassed>"
        "<errorValue>-1</errorValue></QDocRoot>"
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, text=response)
    )

    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(
            QnapAuthenticationError,
            match="用户名或密码",
        ):
            await QnapClient(CONNECTION, http=http).login()


@pytest.mark.asyncio
async def test_owned_http_client_closes_when_context_login_fails():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            text=(
                "<QDocRoot>"
                "<authPassed>0</authPassed>"
                "</QDocRoot>"
            ),
        )
    )
    client = QnapClient(CONNECTION)
    await client.http.aclose()
    client.http = httpx.AsyncClient(
        transport=transport
    )

    with pytest.raises(QnapAuthenticationError):
        await client.__aenter__()

    assert client.http.is_closed


@pytest.mark.asyncio
async def test_logout_connection_failure_does_not_mask_operation():
    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        if request.url.path.endswith(
            "authLogin.cgi"
        ):
            return httpx.Response(
                200,
                text=(
                    "<QDocRoot>"
                    "<authPassed>1</authPassed>"
                    "<authSid>abc123</authSid>"
                    "</QDocRoot>"
                ),
            )
        raise httpx.ConnectError(
            "offline during logout",
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http:
        async with QnapClient(
            CONNECTION,
            http=http,
        ):
            pass


@pytest.mark.asyncio
async def test_client_passes_verify_false_when_tls_bypass_enabled(
    monkeypatch,
):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def aclose(self):
            return None

    monkeypatch.setattr(
        "nas_index.qnap.client.httpx.AsyncClient",
        FakeAsyncClient,
    )

    client = QnapClient(
        NasConnection(
            "https://nas.local",
            5001,
            True,
            "indexer",
            "secret",
            True,
        )
    )
    await client.close()

    assert captured["verify"] is False


@pytest.mark.asyncio
async def test_probe_qnap_connection_prefers_https_for_port_5001(
    monkeypatch,
):
    attempts = []

    async def fake_test_qnap_connection(
        connection,
        *,
        timeout_seconds,
        retry_attempts,
    ):
        attempts.append((connection.base_url, connection.use_https))
        assert timeout_seconds == 20.0
        assert retry_attempts == 3
        return 4

    monkeypatch.setattr(
        "nas_index.qnap.client._test_qnap_connection",
        fake_test_qnap_connection,
    )

    result = await probe_qnap_connection(
        host="192.168.1.16",
        port=5001,
        username="indexer",
        password="secret",
        skip_tls_verify=True,
    )

    assert attempts == [("https://192.168.1.16", True)]
    assert result == QnapProbeResult(
        connection=NasConnection(
            "https://192.168.1.16",
            5001,
            True,
            "indexer",
            "secret",
            True,
        ),
        share_count=4,
    )


@pytest.mark.asyncio
async def test_login_maps_certificate_verification_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "certificate verify failed: hostname mismatch",
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as http:
        with pytest.raises(
            QnapTlsVerificationError,
            match="证书校验失败",
        ):
            await QnapClient(
                NasConnection(
                    "https://nas.local",
                    5001,
                    True,
                    "indexer",
                    "secret",
                ),
                http=http,
                retry_attempts=1,
            ).login()
