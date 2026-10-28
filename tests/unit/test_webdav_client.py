from datetime import datetime

import httpx
import pytest

from nas_index.qnap.errors import (
    QnapAuthenticationError,
    QnapProtocolError,
)
from nas_index.qnap.webdav import WebdavClient
from nas_index.time import BEIJING_TZ
from nas_index.types import NasConnection


CONNECTION = NasConnection(
    "http://nas.local",
    5007,
    False,
    "indexer",
    "secret",
)

MULTISTATUS = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/AI/</D:href>
    <D:propstat>
      <D:prop>
        <D:displayname>AI</D:displayname>
        <D:resourcetype><D:collection/></D:resourcetype>
      </D:prop>
    </D:propstat>
  </D:response>
  <D:response>
    <D:href>/AI/%E5%AD%90%E7%9B%AE%E5%BD%95/</D:href>
    <D:propstat>
      <D:prop>
        <D:displayname>子目录</D:displayname>
        <D:resourcetype><D:collection/></D:resourcetype>
      </D:prop>
    </D:propstat>
  </D:response>
  <D:response>
    <D:href>/AI/report.pdf</D:href>
    <D:propstat>
      <D:prop>
        <D:displayname>report.pdf</D:displayname>
        <D:resourcetype/>
        <D:getcontentlength>123</D:getcontentlength>
        <D:getlastmodified>
          Wed, 21 Oct 2015 07:28:00 GMT
        </D:getlastmodified>
      </D:prop>
    </D:propstat>
  </D:response>
</D:multistatus>
"""


@pytest.mark.asyncio
async def test_iter_children_parses_multistatus_metadata():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["depth"] = request.headers.get("depth")
        return httpx.Response(207, text=MULTISTATUS)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = WebdavClient(CONNECTION, http=http)
        items = [
            item
            async for item in client.iter_children("/AI")
        ]

    assert seen["path"] == "/AI"
    assert seen["depth"] == "1"
    assert [item.name for item in items] == [
        "子目录",
        "report.pdf",
    ]
    directory, file = items
    assert directory.entry_type == "directory"
    assert directory.size_bytes is None
    assert directory.full_path == "/AI/子目录"
    assert directory.parent_path == "/AI"
    assert directory.share_path == "/AI"
    assert file.entry_type == "file"
    assert file.size_bytes == 123
    assert file.modified_at == datetime(
        2015,
        10,
        21,
        15,
        28,
        tzinfo=BEIJING_TZ,
    )


@pytest.mark.asyncio
async def test_iter_children_maps_unauthorized_response():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(401)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = WebdavClient(CONNECTION, http=http)
        with pytest.raises(QnapAuthenticationError):
            _ = [
                item
                async for item in client.iter_children("/AI")
            ]


@pytest.mark.asyncio
async def test_iter_children_rejects_invalid_xml():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(207, content=b"<broken")
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = WebdavClient(CONNECTION, http=http)
        with pytest.raises(QnapProtocolError):
            _ = [
                item
                async for item in client.iter_children("/AI")
            ]


@pytest.mark.asyncio
async def test_iter_children_skips_dot_entries_and_non_children():
    payload = """<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/AI/.DS_Store</D:href>
    <D:propstat><D:prop>
      <D:resourcetype/>
      <D:getcontentlength>1</D:getcontentlength>
    </D:prop></D:propstat>
  </D:response>
  <D:response>
    <D:href>/AI/deep/nested.txt</D:href>
    <D:propstat><D:prop>
      <D:resourcetype/>
      <D:getcontentlength>1</D:getcontentlength>
    </D:prop></D:propstat>
  </D:response>
</D:multistatus>
"""
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(207, text=payload)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = WebdavClient(CONNECTION, http=http)
        items = [
            item
            async for item in client.iter_children("/AI")
        ]

    assert items == []
