from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from nas_index.qnap.client import QnapClient
from nas_index.qnap.errors import (
    QnapAuthenticationError,
    QnapPermissionError,
    QnapProtocolError,
)
from nas_index.types import NasConnection


CONNECTION = NasConnection(
    "http://nas",
    8080,
    False,
    "u",
    "p",
)


@pytest.mark.asyncio
async def test_lists_readable_shares_and_skips_non_folder_nodes():
    payload = [
        {
            "text": "Public",
            "id": "/Public",
            "iconCls": "folder",
            "cls": "r",
        },
        {
            "text": "Archive",
            "id": "/Archive/",
            "iconCls": "folder",
            "cls": "w",
        },
        {
            "text": "DVD",
            "id": "/DVD",
            "iconCls": "odd",
            "cls": "r",
        },
    ]
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json=payload)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = QnapClient(CONNECTION, http=http)
        client.sid = "sid"
        shares = await client.list_shares()

    assert [item.full_path for item in shares] == [
        "/Public",
        "/Archive",
    ]


@pytest.mark.asyncio
async def test_iter_children_follows_total_and_normalizes_metadata():
    starts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["start"])
        starts.append(start)
        rows = (
            [
                {
                    "filename": "子目录",
                    "isfolder": 1,
                    "filesize": "4096",
                    "epochmt": 10,
                }
            ]
            if start == 0
            else [
                {
                    "filename": "a.txt",
                    "isfolder": 0,
                    "filesize": "12",
                    "epochmt": 20,
                }
            ]
        )
        return httpx.Response(
            200,
            json={"total": 2, "datas": rows},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = QnapClient(CONNECTION, http=http)
        client.sid = "sid"
        items = [
            item
            async for item in client.iter_children(
                "/Public",
                page_size=1,
            )
        ]

    assert starts == [0, 1]
    assert items[0].full_path == "/Public/子目录"
    assert items[0].entry_type == "directory"
    assert items[1].size_bytes == 12
    assert items[1].modified_at == datetime(
        1970,
        1,
        1,
        8,
        0,
        20,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "exception"),
    [
        (4, QnapPermissionError),
        (17, QnapAuthenticationError),
    ],
)
async def test_listing_maps_qnap_status(status, exception):
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"status": status, "success": "true"},
        )
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = QnapClient(CONNECTION, http=http)
        client.sid = "sid"
        with pytest.raises(exception):
            _ = [
                item
                async for item in client.iter_children(
                    "/Public",
                    page_size=100,
                )
            ]


@pytest.mark.asyncio
async def test_listing_reports_unknown_qnap_status():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"status": 5, "success": "true"},
        )
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = QnapClient(CONNECTION, http=http)
        client.sid = "sid"
        with pytest.raises(
            QnapProtocolError,
            match="状态码 5",
        ):
            _ = [
                item
                async for item in client.iter_children(
                    "/Public",
                    page_size=100,
                )
            ]


@pytest.mark.asyncio
async def test_listing_retries_transient_connection_failure(monkeypatch):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError(
                "temporary",
                request=request,
            )
        return httpx.Response(200, json=[])

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(
        "nas_index.qnap.client.asyncio.sleep",
        no_sleep,
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = QnapClient(
            CONNECTION,
            http=http,
            retry_attempts=3,
        )
        client.sid = "sid"
        assert await client.list_shares() == []

    assert attempts == 3


@pytest.mark.asyncio
async def test_listing_percent_encodes_spaces_in_path():
    seen_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url
        seen_url = str(request.url)
        return httpx.Response(
            200,
            json={"total": 0, "datas": []},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = QnapClient(CONNECTION, http=http)
        client.sid = "sid"
        _ = [
            item
            async for item in client.iter_children(
                "/Public/Space Folder",
                page_size=100,
            )
        ]

    assert "path=%2FPublic%2FSpace%20Folder" in seen_url


@pytest.mark.asyncio
async def test_get_thumbnail_requests_qnap_thumb_with_parent_and_name():
    seen_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url
        seen_url = str(request.url)
        return httpx.Response(
            200,
            content=b"jpeg-bytes",
            headers={"content-type": "image/jpeg"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = QnapClient(CONNECTION, http=http)
        client.sid = "sid"
        thumbnail = await client.get_thumbnail(
            "/Public/设计图/苹果 主图.jpg",
            size=256,
        )

    assert thumbnail.content == b"jpeg-bytes"
    assert thumbnail.media_type == "image/jpeg"
    assert "func=get_thumb" in seen_url
    assert "sid=sid" in seen_url
    assert "path=%2FPublic%2F%E8%AE%BE%E8%AE%A1%E5%9B%BE" in seen_url
    assert "name=%E8%8B%B9%E6%9E%9C%20%E4%B8%BB%E5%9B%BE.jpg" in seen_url
    assert "size=256" in seen_url


@pytest.mark.asyncio
async def test_download_file_requests_qnap_download_with_parent_and_name():
    seen_url = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url
        seen_url = str(request.url)
        return httpx.Response(
            200,
            content=b"original-bytes",
            headers={"content-type": "application/force-download"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = QnapClient(CONNECTION, http=http)
        client.sid = "sid"
        downloaded = await client.download_file(
            "/Public/设计图/苹果 主图.jpg",
        )

    assert downloaded.content == b"original-bytes"
    assert downloaded.media_type == "application/force-download"
    assert "func=download" in seen_url
    assert "sid=sid" in seen_url
    assert "isfolder=0" in seen_url
    assert "source_path=%2FPublic%2F%E8%AE%BE%E8%AE%A1%E5%9B%BE" in seen_url
    assert "source_file=%E8%8B%B9%E6%9E%9C%20%E4%B8%BB%E5%9B%BE.jpg" in seen_url
    assert "source_total=1" in seen_url


def test_qnap_client_ignores_environment_proxy_by_default(monkeypatch):
    client_kwargs = []

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            client_kwargs.append(kwargs)

    monkeypatch.setattr(
        "nas_index.qnap.client.httpx.AsyncClient",
        FakeAsyncClient,
    )

    QnapClient(CONNECTION)

    assert client_kwargs[0]["trust_env"] is False
