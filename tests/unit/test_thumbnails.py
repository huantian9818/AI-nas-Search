from datetime import UTC, datetime
from hashlib import sha256

import pytest
from PIL import Image

from nas_index.models import Entry
from nas_index.services import thumbnails
from nas_index.services.thumbnails import ThumbnailService
from nas_index.types import NasConnection


PNG_IMAGE_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05"
    b"\xfe\x02\xfeA\xe2\x8a\xf0\x00\x00\x00\x00IEND\xaeB`\x82"
)

QNAP_PLACEHOLDER_320_SHA256 = (
    "d4a92e2b35d043257cf69936a2226f253a5483cda0e32d82a7bb8fb271cb35b5"
)
QNAP_PLACEHOLDER_640_SHA256 = (
    "924fc48a022885326820457f013a875ec2761f9ae1e63652990b0f902b74546d"
)
LOCAL_PLACEHOLDER_JPEG_SHA256 = (
    "42b97c86277efa74e554c2e67da63e41f96cedc5af2722233d9aa1386d71aebe"
)


class FakeQnapClient:
    def __init__(self):
        self.calls = []

    async def get_thumbnail(
        self,
        full_path: str,
        *,
        size: int,
    ):
        self.calls.append((full_path, size))
        return type(
            "Thumbnail",
            (),
            {
                "content": PNG_IMAGE_BYTES,
                "media_type": "image/png",
            },
        )()


class PlaceholderQnapClient:
    def __init__(self, content: bytes):
        self.content = content
        self.calls = []
        self.download_calls = []

    async def get_thumbnail(
        self,
        full_path: str,
        *,
        size: int,
    ):
        self.calls.append((full_path, size))
        return type(
            "Thumbnail",
            (),
            {
                "content": self.content,
                "media_type": "image/png",
            },
        )()

    async def download_file(
        self,
        full_path: str,
    ):
        self.download_calls.append(full_path)
        return type(
            "DownloadedFile",
            (),
            {
                "content": PNG_IMAGE_BYTES,
                "media_type": "image/png",
            },
        )()


def test_known_qnap_placeholder_hashes_are_rejected():
    assert (
        QNAP_PLACEHOLDER_320_SHA256
        in thumbnails.PLACEHOLDER_THUMBNAIL_SHA256
    )
    assert (
        QNAP_PLACEHOLDER_640_SHA256
        in thumbnails.PLACEHOLDER_THUMBNAIL_SHA256
    )
    assert (
        LOCAL_PLACEHOLDER_JPEG_SHA256
        in thumbnails.PLACEHOLDER_THUMBNAIL_SHA256
    )


@pytest.mark.asyncio
async def test_thumbnail_service_caches_by_entry_metadata(tmp_path):
    fake_client = FakeQnapClient()
    service = ThumbnailService(
        cache_dir=tmp_path,
        client_factory=lambda _connection: fake_client,
    )
    entry = Entry(
        id=12,
        nas_id=3,
        share_path="/Public",
        name="苹果.jpg",
        full_path="/Public/苹果.jpg",
        parent_path="/Public",
        entry_type="file",
        size_bytes=42,
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
        scan_generation=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    connection = NasConnection(
        base_url="http://nas",
        port=5000,
        use_https=False,
        username="indexer",
        password="secret",
    )

    first = await service.get(entry, connection)
    second = await service.get(entry, connection)

    assert first is not None
    assert second is not None
    assert first.path == second.path
    assert first.media_type == "image/png"
    assert first.path.read_bytes() == PNG_IMAGE_BYTES
    assert fake_client.calls == [
        ("/Public/苹果.jpg", 80),
    ]


@pytest.mark.asyncio
async def test_thumbnail_service_logs_qnap_thumb_and_cache_hit(
    tmp_path,
    monkeypatch,
):
    messages = []
    monkeypatch.setattr(
        thumbnails.LOGGER,
        "info",
        lambda message, *args: messages.append(message % args),
    )
    fake_client = FakeQnapClient()
    service = ThumbnailService(
        cache_dir=tmp_path,
        client_factory=lambda _connection: fake_client,
    )
    entry = Entry(
        id=16,
        nas_id=3,
        share_path="/Public",
        name="苹果.jpg",
        full_path="/Public/苹果.jpg",
        parent_path="/Public",
        entry_type="file",
        size_bytes=42,
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
        scan_generation=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    connection = NasConnection(
        base_url="http://nas",
        port=5000,
        use_https=False,
        username="indexer",
        password="secret",
    )

    await service.get(entry, connection)
    await service.get(entry, connection)

    assert any("source=qnap_thumb" in message for message in messages)
    assert any("source=cache_hit" in message for message in messages)
    assert all("secret" not in message for message in messages)


@pytest.mark.asyncio
async def test_thumbnail_service_cache_key_includes_output_size(tmp_path):
    fake_client = FakeQnapClient()
    connection = NasConnection(
        base_url="http://nas",
        port=5000,
        use_https=False,
        username="indexer",
        password="secret",
    )
    entry = Entry(
        id=15,
        nas_id=3,
        share_path="/Public",
        name="葡萄.png",
        full_path="/Public/葡萄.png",
        parent_path="/Public",
        entry_type="file",
        size_bytes=42,
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
        scan_generation=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    first = await ThumbnailService(
        cache_dir=tmp_path,
        client_factory=lambda _connection: fake_client,
        size=160,
    ).get(entry, connection)
    second = await ThumbnailService(
        cache_dir=tmp_path,
        client_factory=lambda _connection: fake_client,
        size=80,
    ).get(entry, connection)

    assert first is not None
    assert second is not None
    assert first.path != second.path
    assert fake_client.calls == [
        ("/Public/葡萄.png", 160),
        ("/Public/葡萄.png", 80),
    ]


@pytest.mark.asyncio
async def test_thumbnail_service_skips_non_image_files(tmp_path):
    fake_client = FakeQnapClient()
    service = ThumbnailService(
        cache_dir=tmp_path,
        client_factory=lambda _connection: fake_client,
    )
    entry = Entry(
        id=13,
        nas_id=3,
        share_path="/Public",
        name="表格.xlsx",
        full_path="/Public/表格.xlsx",
        parent_path="/Public",
        entry_type="file",
        size_bytes=42,
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
        scan_generation=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    connection = NasConnection(
        base_url="http://nas",
        port=5000,
        use_https=False,
        username="indexer",
        password="secret",
    )

    assert await service.get(entry, connection) is None
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_thumbnail_service_generates_local_thumb_for_qnap_placeholder(
    tmp_path,
    monkeypatch,
):
    messages = []
    monkeypatch.setattr(
        thumbnails.LOGGER,
        "info",
        lambda message, *args: messages.append(message % args),
    )
    placeholder = b"qnap-placeholder"
    monkeypatch.setattr(
        thumbnails,
        "PLACEHOLDER_THUMBNAIL_SHA256",
        {sha256(placeholder).hexdigest()},
    )
    fake_client = PlaceholderQnapClient(placeholder)
    service = ThumbnailService(
        cache_dir=tmp_path,
        client_factory=lambda _connection: fake_client,
    )
    entry = Entry(
        id=14,
        nas_id=3,
        share_path="/Public",
        name="最终成图.jpg",
        full_path="/Public/最终成图.jpg",
        parent_path="/Public",
        entry_type="file",
        size_bytes=42,
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
        scan_generation=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    connection = NasConnection(
        base_url="http://nas",
        port=5000,
        use_https=False,
        username="indexer",
        password="secret",
    )

    result = await service.get(entry, connection)

    assert result is not None
    assert result.media_type == "image/jpeg"
    assert result.path.read_bytes().startswith(b"\xff\xd8")
    with Image.open(result.path) as image:
        assert max(image.size) <= 80
    assert fake_client.calls == [
        ("/Public/最终成图.jpg", 80),
    ]
    assert fake_client.download_calls == [
        "/Public/最终成图.jpg",
    ]
    assert any(
        "source=fallback_original" in message
        for message in messages
    )
