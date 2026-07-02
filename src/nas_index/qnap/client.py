import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import AsyncIterator
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

import httpx

from nas_index.qnap.errors import (
    QnapAuthenticationError,
    QnapConnectionError,
    QnapPermissionError,
    QnapProtocolError,
    QnapSessionExpired,
    QnapTwoStepRequired,
)
from nas_index.time import from_timestamp_beijing
from nas_index.types import IndexedItem, NasConnection


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class QnapThumbnail:
    content: bytes
    media_type: str


@dataclass(frozen=True, slots=True)
class QnapFile:
    content: bytes
    media_type: str


def canonical_path(value: str) -> str:
    parts = [
        part
        for part in PurePosixPath(
            value.replace("\\", "/")
        ).parts
        if part != "/"
    ]
    return "/" + "/".join(parts)


def join_path(parent: str, name: str) -> str:
    return canonical_path(f"{parent}/{name}")


def _payload_excerpt(
    payload: object,
    limit: int = 600,
) -> str:
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
        )
    except TypeError:
        text = repr(payload)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _encode_query_value(value: object) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _build_request_url(
    url: str,
    params: dict[str, object],
) -> str:
    encoded_params: dict[str, object] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            encoded_params[key] = [
                _encode_query_value(item)
                for item in value
            ]
        else:
            encoded_params[key] = _encode_query_value(value)
    query = urlencode(
        encoded_params,
        doseq=True,
        quote_via=quote,
    )
    if not query:
        return url
    return f"{url}?{query}"


def build_download_url(
    *,
    endpoint: str,
    sid: str,
    full_path: str,
    is_directory: bool = False,
) -> str:
    normalized = canonical_path(full_path)
    path = PurePosixPath(normalized)
    parent_path = canonical_path(str(path.parent))
    if not is_directory:
        return build_batch_download_url(
            endpoint=endpoint,
            sid=sid,
            parent_path=parent_path,
            source_files=(path.name,),
        )
    return _build_request_url(
        f"{endpoint}/cgi-bin/filemanager/utilRequest.cgi",
        {
            "func": "download",
            "isfolder": 1,
            "source_path": parent_path,
            "source_file": path.name,
            "source_total": 1,
            "sid": sid,
        },
    )


def build_batch_download_url(
    *,
    endpoint: str,
    sid: str,
    parent_path: str,
    source_files: tuple[str, ...],
) -> str:
    if not source_files:
        raise ValueError("source_files must not be empty")
    return _build_request_url(
        f"{endpoint}/cgi-bin/filemanager/utilRequest.cgi",
        {
            "func": "download",
            "isfolder": 0,
            "source_path": canonical_path(parent_path),
            "source_file": source_files,
            "source_total": len(source_files),
            "sid": sid,
        },
    )


def _raise_for_qnap_status(
    payload: object,
    *,
    func: object | None,
    path: object | None,
) -> None:
    if not isinstance(payload, dict):
        return
    status = payload.get("status")
    if status == 4:
        raise QnapPermissionError()
    if status == 17:
        raise QnapAuthenticationError()
    if status not in {None, 0, 1}:
        LOGGER.warning(
            "QNAP returned unexpected status "
            "func=%s path=%s status=%r payload=%s",
            func or "-",
            path or "-",
            status,
            _payload_excerpt(payload),
        )
        raise QnapProtocolError(status=status)


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
        self.http = http or httpx.AsyncClient(
            timeout=timeout_seconds,
            trust_env=False,
        )
        self._owns_http = http is None
        self.retry_attempts = max(1, min(retry_attempts, 3))
        self.sid: str | None = None

    async def login(self) -> str:
        encoded = base64.b64encode(
            self.connection.password.encode("utf-8")
        ).decode("ascii")
        response = await self._request_with_retry(
            f"{self.connection.endpoint}/cgi-bin/authLogin.cgi",
            {
                "user": self.connection.username,
                "pwd": encoded,
                "remme": 0,
                "serviceKey": 1,
            },
        )

        try:
            root = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as exc:
            LOGGER.warning(
                "QNAP login returned invalid XML body=%s",
                response.text[:500],
            )
            raise QnapProtocolError() from exc

        if root.findtext("need_2sv") == "1":
            raise QnapTwoStepRequired()
        if root.findtext("authPassed") != "1":
            raise QnapAuthenticationError()

        sid = root.findtext("authSid")
        if not sid:
            LOGGER.warning(
                "QNAP login response was missing sid body=%s",
                response.text[:500],
            )
            raise QnapProtocolError()
        self.sid = sid
        return sid

    async def list_shares(self) -> list[IndexedItem]:
        payload = await self._file_station_request(
            {
                "func": "get_tree",
                "node": "share_root",
                "is_iso": 0,
                "hidden_file": 0,
            }
        )
        if not isinstance(payload, list):
            LOGGER.warning(
                "QNAP share listing returned unexpected payload "
                "payload=%s",
                _payload_excerpt(payload),
            )
            raise QnapProtocolError()
        shares: list[IndexedItem] = []
        for row in payload:
            if row.get("iconCls") != "folder" or row.get(
                "cls"
            ) not in {"r", "w"}:
                continue
            try:
                full_path = canonical_path(str(row["id"]))
                shares.append(
                    IndexedItem(
                        name=str(row["text"]),
                        full_path=full_path,
                        parent_path="/",
                        entry_type="directory",
                        size_bytes=None,
                        modified_at=None,
                        share_path=full_path,
                    )
                )
            except (
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                LOGGER.warning(
                    "QNAP share row contained invalid metadata "
                    "row=%s",
                    _payload_excerpt(row),
                )
                raise QnapProtocolError() from exc
        return shares

    async def validate_sid(self) -> None:
        try:
            payload = await self._file_station_request(
                {
                    "func": "get_tree",
                    "node": "share_root",
                    "is_iso": 0,
                    "hidden_file": 0,
                }
            )
        except QnapProtocolError as exc:
            if exc.status == 3:
                raise QnapSessionExpired() from exc
            raise
        if not isinstance(payload, list):
            LOGGER.warning(
                "QNAP sid validation returned unexpected payload "
                "payload=%s",
                _payload_excerpt(payload),
            )
            raise QnapProtocolError()

    async def iter_children(
        self,
        path: str,
        *,
        page_size: int,
    ) -> AsyncIterator[IndexedItem]:
        start = 0
        current_path = canonical_path(path)
        path_parts = [
            part
            for part in PurePosixPath(current_path).parts
            if part != "/"
        ]
        share_path = (
            f"/{path_parts[0]}"
            if path_parts
            else "/"
        )
        while True:
            payload = await self._file_station_request(
                {
                    "func": "get_list",
                    "is_iso": 0,
                    "list_mode": "all",
                    "path": current_path,
                    "dir": "ASC",
                    "limit": page_size,
                    "sort": "filename",
                    "start": start,
                    "hidden_file": 0,
                    "v": 1,
                }
            )
            if not isinstance(payload, dict) or not isinstance(
                payload.get("datas"),
                list,
            ):
                LOGGER.warning(
                    "QNAP directory listing returned unexpected "
                    "payload path=%s payload=%s",
                    path,
                    _payload_excerpt(payload),
                )
                raise QnapProtocolError()

            rows = payload["datas"]
            for row in rows:
                try:
                    is_directory = (
                        int(row.get("isfolder", 0)) == 1
                    )
                    epoch = int(row.get("epochmt") or 0)
                    name = str(row["filename"])
                    size_bytes = (
                        None
                        if is_directory
                        else int(row.get("filesize") or 0)
                    )
                except (
                    KeyError,
                    TypeError,
                    ValueError,
                ) as exc:
                    LOGGER.warning(
                        "QNAP directory row contained invalid "
                        "metadata path=%s row=%s",
                        path,
                        _payload_excerpt(row),
                    )
                    raise QnapProtocolError() from exc
                yield IndexedItem(
                    name=name,
                    full_path=join_path(
                        path,
                        name,
                    ),
                    parent_path=canonical_path(path),
                    entry_type=(
                        "directory" if is_directory else "file"
                    ),
                    size_bytes=size_bytes,
                    modified_at=(
                        from_timestamp_beijing(epoch)
                        if epoch
                        else None
                    ),
                    share_path=share_path,
                )

            start += len(rows)
            try:
                total = int(payload.get("total", start))
            except (TypeError, ValueError) as exc:
                LOGGER.warning(
                    "QNAP directory listing returned invalid "
                    "total path=%s payload=%s",
                    path,
                    _payload_excerpt(payload),
                )
                raise QnapProtocolError() from exc
            if not rows or start >= total:
                break

    async def get_thumbnail(
        self,
        full_path: str,
        *,
        size: int = 256,
    ) -> QnapThumbnail:
        if not self.sid:
            raise QnapAuthenticationError()
        normalized = canonical_path(full_path)
        path = PurePosixPath(normalized)
        parent_path = canonical_path(str(path.parent))
        response = await self._request_with_retry(
            (
                f"{self.connection.endpoint}"
                "/cgi-bin/filemanager/utilRequest.cgi"
            ),
            {
                "func": "get_thumb",
                "path": parent_path,
                "name": path.name,
                "size": size,
                "sid": self.sid,
            },
        )
        media_type = response.headers.get(
            "content-type",
            "image/jpeg",
        ).split(";")[0].strip().lower()
        if not response.content or not media_type.startswith("image/"):
            raise QnapProtocolError()
        return QnapThumbnail(
            content=response.content,
            media_type=media_type,
        )

    async def download_file(
        self,
        full_path: str,
    ) -> QnapFile:
        if not self.sid:
            raise QnapAuthenticationError()
        normalized = canonical_path(full_path)
        path = PurePosixPath(normalized)
        parent_path = canonical_path(str(path.parent))
        response = await self._request_with_retry(
            (
                f"{self.connection.endpoint}"
                "/cgi-bin/filemanager/utilRequest.cgi"
            ),
            {
                "func": "download",
                "isfolder": 0,
                "source_path": parent_path,
                "source_file": path.name,
                "source_total": 1,
                "sid": self.sid,
            },
        )
        media_type = response.headers.get(
            "content-type",
            "application/octet-stream",
        ).split(";")[0].strip().lower()
        if media_type == "application/json":
            try:
                payload = response.json()
            except ValueError as exc:
                raise QnapProtocolError() from exc
            _raise_for_qnap_status(
                payload,
                func="download",
                path=parent_path,
            )
            raise QnapProtocolError()
        if not response.content:
            raise QnapProtocolError()
        return QnapFile(
            content=response.content,
            media_type=media_type,
        )

    def download_url(
        self,
        full_path: str,
        *,
        is_directory: bool = False,
    ) -> str:
        if not self.sid:
            raise QnapAuthenticationError()
        return build_download_url(
            endpoint=self.connection.endpoint,
            sid=self.sid,
            full_path=full_path,
            is_directory=is_directory,
        )

    async def _file_station_request(
        self,
        params: dict[str, object],
    ) -> object:
        if not self.sid:
            raise QnapAuthenticationError()
        response = await self._request_with_retry(
            (
                f"{self.connection.endpoint}"
                "/cgi-bin/filemanager/utilRequest.cgi"
            ),
            {**params, "sid": self.sid},
        )
        try:
            payload = response.json()
        except ValueError as exc:
            LOGGER.warning(
                "QNAP returned non-JSON payload func=%s path=%s "
                "body=%s",
                params.get("func", "-"),
                params.get("path", "-"),
                response.text[:500],
            )
            raise QnapProtocolError() from exc
        _raise_for_qnap_status(
            payload,
            func=params.get("func"),
            path=params.get("path"),
        )
        return payload

    async def _request_with_retry(
        self,
        url: str,
        params: dict[str, object],
    ) -> httpx.Response:
        delays = (0.0, 0.25, 0.75)[: self.retry_attempts]
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                request_url = _build_request_url(
                    url,
                    params,
                )
                response = await self.http.get(
                    request_url,
                )
                if response.status_code in {502, 503, 504}:
                    if attempt == len(delays):
                        raise QnapConnectionError()
                    continue
                response.raise_for_status()
                return response
            except (
                httpx.TimeoutException,
                httpx.ConnectError,
            ) as exc:
                if attempt == len(delays):
                    raise QnapConnectionError() from exc
            except httpx.HTTPStatusError as exc:
                raise QnapConnectionError() from exc
        raise QnapConnectionError()

    async def logout(self) -> None:
        if self.sid:
            try:
                await self.http.get(
                    f"{self.connection.endpoint}/cgi-bin/authLogout.cgi",
                    params={"sid": self.sid},
                )
            except httpx.HTTPError:
                pass
            finally:
                self.sid = None
        if self._owns_http:
            await self.http.aclose()

    async def close(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def __aenter__(self) -> "QnapClient":
        try:
            await self.login()
        except BaseException:
            if self._owns_http:
                await self.http.aclose()
            raise
        return self

    async def __aexit__(self, *_exc_info) -> None:
        await self.logout()
