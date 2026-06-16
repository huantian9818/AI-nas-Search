from datetime import UTC, datetime

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
    assert items[1].modified_at == datetime.fromtimestamp(20, UTC)


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
