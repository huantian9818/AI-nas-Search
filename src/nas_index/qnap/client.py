import base64
from xml.etree import ElementTree

import httpx

from nas_index.qnap.errors import (
    QnapAuthenticationError,
    QnapConnectionError,
    QnapProtocolError,
    QnapTwoStepRequired,
)
from nas_index.types import NasConnection


class QnapClient:
    def __init__(
        self,
        connection: NasConnection,
        *,
        http: httpx.AsyncClient | None = None,
        timeout_seconds: float = 20.0,
        retry_attempts: int = 3,
    ):
        self.connection = connection
        self.http = http or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_http = http is None
        self.retry_attempts = max(1, min(retry_attempts, 3))
        self.sid: str | None = None

    async def login(self) -> str:
        encoded = base64.b64encode(
            self.connection.password.encode("utf-8")
        ).decode("ascii")
        try:
            response = await self.http.get(
                f"{self.connection.endpoint}/cgi-bin/authLogin.cgi",
                params={
                    "user": self.connection.username,
                    "pwd": encoded,
                    "remme": 0,
                    "serviceKey": 1,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise QnapConnectionError() from exc

        try:
            root = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as exc:
            raise QnapProtocolError() from exc

        if root.findtext("need_2sv") == "1":
            raise QnapTwoStepRequired()
        if root.findtext("authPassed") != "1":
            raise QnapAuthenticationError()

        sid = root.findtext("authSid")
        if not sid:
            raise QnapProtocolError()
        self.sid = sid
        return sid

    async def logout(self) -> None:
        if self.sid:
            try:
                await self.http.get(
                    f"{self.connection.endpoint}/cgi-bin/authLogout.cgi",
                    params={"sid": self.sid},
                )
            finally:
                self.sid = None
        if self._owns_http:
            await self.http.aclose()

    async def __aenter__(self) -> "QnapClient":
        await self.login()
        return self

    async def __aexit__(self, *_exc_info) -> None:
        await self.logout()
