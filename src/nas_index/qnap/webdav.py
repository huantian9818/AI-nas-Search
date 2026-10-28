import asyncio
import logging
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

import httpx

from nas_index.qnap.client import canonical_path
from nas_index.qnap.errors import (
    QnapAuthenticationError,
    QnapConnectionError,
    QnapPermissionError,
    QnapProtocolError,
)
from nas_index.time import BEIJING_TZ
from nas_index.types import IndexedItem, NasConnection


LOGGER = logging.getLogger(__name__)

PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:displayname/>"
    "<d:resourcetype/>"
    "<d:getcontentlength/>"
    "<d:getlastmodified/>"
    "</d:prop></d:propfind>"
)


def _dav(tag: str) -> str:
    return f"{{DAV:}}{tag}"


class WebdavClient:
    """Read-only WebDAV directory lister.

    QNAP's Apache has ``DavDepthInfinity`` disabled, so the only available
    primitive is a ``Depth: 1`` PROPFIND per directory. Each response already
    carries name, size, mtime and resource type, so no per-entry ``stat`` is
    needed.
    """

    def __init__(
        self,
        connection: NasConnection,
        *,
        http: httpx.AsyncClient | None = None,
        timeout_seconds: float = 20.0,
        retry_attempts: int = 3,
        max_connections: int | None = None,
    ):
        self.connection = connection
        self.endpoint = connection.endpoint
        client_kwargs: dict[str, object] = {
            "auth": (
                connection.username,
                connection.password,
            ),
            "timeout": timeout_seconds,
            "trust_env": False,
            "verify": not connection.skip_tls_verify,
        }
        if max_connections is not None:
            client_kwargs["limits"] = httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections,
            )
        self.http = http or httpx.AsyncClient(**client_kwargs)
        self._owns_http = http is None
        self.retry_attempts = max(1, min(retry_attempts, 3))

    async def iter_children(
        self,
        path: str,
        *,
        page_size: int = 0,
    ):
        payload = await self._propfind(path)
        for item in parse_multistatus(payload, path):
            yield item

    async def _propfind(self, path: str) -> bytes:
        url = (
            f"{self.endpoint}"
            f"{quote(canonical_path(path), safe='/')}"
        )
        delays = (0.0, 0.5, 1.5)[: self.retry_attempts]
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                await asyncio.sleep(delay)
            try:
                response = await self.http.request(
                    "PROPFIND",
                    url,
                    content=PROPFIND_BODY,
                    headers={
                        "Depth": "1",
                        "Content-Type": "application/xml",
                    },
                )
                if response.status_code in {502, 503, 504}:
                    if attempt == len(delays):
                        raise QnapConnectionError()
                    continue
                if response.status_code == 401:
                    raise QnapAuthenticationError()
                if response.status_code == 403:
                    raise QnapPermissionError()
                if response.status_code != 207:
                    LOGGER.warning(
                        "WebDAV PROPFIND failed path=%s status=%s",
                        path,
                        response.status_code,
                    )
                    raise QnapProtocolError(
                        status=response.status_code
                    )
                return response.content
            except httpx.TransportError as exc:
                if attempt == len(delays):
                    raise QnapConnectionError() from exc
        raise QnapConnectionError()

    async def aclose(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def __aenter__(self) -> "WebdavClient":
        return self

    async def __aexit__(self, *_exc_info) -> None:
        await self.aclose()


def parse_multistatus(
    payload: bytes,
    requested_path: str,
) -> list[IndexedItem]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        LOGGER.warning(
            "WebDAV returned invalid XML path=%s body=%s",
            requested_path,
            payload[:300],
        )
        raise QnapProtocolError() from exc

    parent_path = canonical_path(requested_path)
    parts = [
        part
        for part in PurePosixPath(parent_path).parts
        if part != "/"
    ]
    share_path = f"/{parts[0]}" if parts else "/"

    items: list[IndexedItem] = []
    seen: set[str] = set()
    for node in root.findall(_dav("response")):
        href = node.findtext(_dav("href"))
        if not href:
            continue
        href_path = unquote(urlsplit(href).path)
        full_path = canonical_path(href_path)
        if full_path == parent_path or full_path in seen:
            continue
        if full_path.rsplit("/", 1)[0] != parent_path:
            continue

        prop = node.find(
            f"{_dav('propstat')}/{_dav('prop')}"
        )
        if prop is None:
            continue

        resource_type = prop.find(_dav("resourcetype"))
        is_directory = (
            resource_type is not None
            and resource_type.find(_dav("collection"))
            is not None
        )
        name = (
            prop.findtext(_dav("displayname")) or ""
        ).strip() or PurePosixPath(full_path).name
        if not name or name.startswith("."):
            continue

        size_bytes: int | None = None
        if not is_directory:
            size_text = prop.findtext(
                _dav("getcontentlength")
            )
            if size_text:
                try:
                    size_bytes = int(size_text)
                except ValueError:
                    size_bytes = None

        modified_at = _parse_modified(
            prop.findtext(_dav("getlastmodified"))
        )
        seen.add(full_path)
        items.append(
            IndexedItem(
                name=name,
                full_path=full_path,
                parent_path=parent_path,
                entry_type=(
                    "directory"
                    if is_directory
                    else "file"
                ),
                size_bytes=size_bytes,
                modified_at=modified_at,
                share_path=share_path,
            )
        )
    return items


def _parse_modified(value: str | None):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(BEIJING_TZ)
