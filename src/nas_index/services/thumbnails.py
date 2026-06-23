from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import logging
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps, UnidentifiedImageError

from nas_index.models import Entry
from nas_index.qnap.client import QnapClient
from nas_index.qnap.errors import (
    QnapAuthenticationError,
    QnapConnectionError,
    QnapPermissionError,
    QnapProtocolError,
)
from nas_index.types import NasConnection


LOGGER = logging.getLogger(__name__)


IMAGE_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
}

PLACEHOLDER_THUMBNAIL_SHA256 = {
    "cd950d0f322847ec6e5c4ac4387d90ed62b57c0d576ed00515caa7130165cbc6",
    "d4a92e2b35d043257cf69936a2226f253a5483cda0e32d82a7bb8fb271cb35b5",
    "924fc48a022885326820457f013a875ec2761f9ae1e63652990b0f902b74546d",
    "42b97c86277efa74e554c2e67da63e41f96cedc5af2722233d9aa1386d71aebe",
}


class ThumbnailClient(Protocol):
    async def get_thumbnail(
        self,
        full_path: str,
        *,
        size: int,
    ):
        ...

    async def download_file(
        self,
        full_path: str,
    ):
        ...


@dataclass(frozen=True, slots=True)
class ThumbnailResult:
    path: Path
    media_type: str


def is_thumbnail_candidate(entry: Entry) -> bool:
    if entry.entry_type != "file":
        return False
    return Path(entry.name).suffix.lower() in IMAGE_SUFFIXES


class ThumbnailService:
    def __init__(
        self,
        *,
        cache_dir: Path,
        client_factory: Callable[[NasConnection], ThumbnailClient],
        size: int = 80,
        qnap_size: int | None = None,
    ):
        self.cache_dir = cache_dir
        self.client_factory = client_factory
        self.size = size
        self.qnap_size = qnap_size or size

    async def get(
        self,
        entry: Entry,
        connection: NasConnection,
    ) -> ThumbnailResult | None:
        if not is_thumbnail_candidate(entry):
            _log_thumbnail(
                entry,
                source="skip",
                reason="unsupported_type",
            )
            return None

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        key = _cache_key(
            entry,
            size=self.size,
        )
        content_path = self.cache_dir / f"{key}.bin"
        media_type_path = self.cache_dir / f"{key}.mime"
        if content_path.exists() and media_type_path.exists():
            content = content_path.read_bytes()
            media_type = media_type_path.read_text(
                encoding="utf-8",
            ).strip() or "image/jpeg"
            if _is_placeholder_thumbnail(
                content,
                media_type,
            ):
                _log_thumbnail(
                    entry,
                    source="cache_reject",
                    reason="placeholder",
                    media_type=media_type,
                    bytes_count=len(content),
                )
                content_path.unlink(missing_ok=True)
                media_type_path.unlink(missing_ok=True)
            else:
                _log_thumbnail(
                    entry,
                    source="cache_hit",
                    media_type=media_type,
                    bytes_count=len(content),
                )
                return ThumbnailResult(
                    path=content_path,
                    media_type=media_type,
                )

        try:
            thumbnail = await self._fetch_thumbnail(
                entry,
                connection,
            )
        except (
            QnapAuthenticationError,
            QnapConnectionError,
            QnapPermissionError,
            QnapProtocolError,
        ) as exc:
            _log_thumbnail(
                entry,
                source="qnap_error",
                reason=type(exc).__name__,
            )
            return None

        if _is_placeholder_thumbnail(
            thumbnail.content,
            thumbnail.media_type,
        ):
            generated = await self._generate_from_original(
                entry,
                connection,
            )
            if generated is None:
                _log_thumbnail(
                    entry,
                    source="fallback_failed",
                    reason="original_unavailable",
                    qnap_media_type=thumbnail.media_type,
                    qnap_bytes=len(thumbnail.content),
                    output_size=self.size,
                )
                return None
            content = generated
            media_type = "image/jpeg"
            source = "fallback_original"
        else:
            content = thumbnail.content
            media_type = thumbnail.media_type
            if self.qnap_size != self.size:
                try:
                    content = _create_local_thumbnail(
                        thumbnail.content,
                        self.size,
                    )
                except (OSError, UnidentifiedImageError):
                    _log_thumbnail(
                        entry,
                        source="qnap_error",
                        reason="invalid_qnap_thumbnail",
                        qnap_media_type=thumbnail.media_type,
                        qnap_bytes=len(thumbnail.content),
                        output_size=self.size,
                    )
                    return None
                media_type = "image/jpeg"

            if _is_placeholder_thumbnail(
                content,
                media_type,
            ):
                _log_thumbnail(
                    entry,
                    source="qnap_reject",
                    reason="placeholder_after_resize",
                    media_type=media_type,
                    bytes_count=len(content),
                    qnap_size=self.qnap_size,
                    output_size=self.size,
                )
                return None
            source = "qnap_thumb"

        content_path.write_bytes(content)
        media_type_path.write_text(
            media_type,
            encoding="utf-8",
        )
        _log_thumbnail(
            entry,
            source=source,
            media_type=media_type,
            bytes_count=len(content),
            qnap_size=self.qnap_size,
            output_size=self.size,
        )
        return ThumbnailResult(
            path=content_path,
            media_type=media_type,
        )

    async def _fetch_thumbnail(
        self,
        entry: Entry,
        connection: NasConnection,
    ):
        client = self.client_factory(connection)
        if isinstance(client, QnapClient):
            async with client as logged_in_client:
                return await logged_in_client.get_thumbnail(
                    entry.full_path,
                    size=self.qnap_size,
                )
        return await client.get_thumbnail(
            entry.full_path,
            size=self.qnap_size,
        )

    async def _generate_from_original(
        self,
        entry: Entry,
        connection: NasConnection,
    ) -> bytes | None:
        try:
            original = await self._download_original(
                entry,
                connection,
            )
            return _create_local_thumbnail(
                original.content,
                self.size,
            )
        except (
            OSError,
            QnapAuthenticationError,
            QnapConnectionError,
            QnapPermissionError,
            QnapProtocolError,
            UnidentifiedImageError,
        ):
            return None

    async def _download_original(
        self,
        entry: Entry,
        connection: NasConnection,
    ):
        client = self.client_factory(connection)
        if isinstance(client, QnapClient):
            async with client as logged_in_client:
                return await logged_in_client.download_file(
                    entry.full_path,
                )
        return await client.download_file(entry.full_path)


def _log_thumbnail(
    entry: Entry,
    *,
    source: str,
    media_type: str | None = None,
    bytes_count: int | None = None,
    reason: str | None = None,
    qnap_size: int | None = None,
    output_size: int | None = None,
    qnap_media_type: str | None = None,
    qnap_bytes: int | None = None,
) -> None:
    details = [
        f"source={source}",
        f"entry_id={entry.id}",
        f"nas_id={entry.nas_id}",
        f"path={entry.full_path}",
    ]
    if media_type is not None:
        details.append(f"media_type={media_type}")
    if bytes_count is not None:
        details.append(f"bytes={bytes_count}")
    if reason is not None:
        details.append(f"reason={reason}")
    if qnap_size is not None:
        details.append(f"qnap_size={qnap_size}")
    if output_size is not None:
        details.append(f"output_size={output_size}")
    if qnap_media_type is not None:
        details.append(f"qnap_media_type={qnap_media_type}")
    if qnap_bytes is not None:
        details.append(f"qnap_bytes={qnap_bytes}")
    LOGGER.info(
        "thumbnail %s",
        " ".join(details),
    )


def _create_local_thumbnail(
    content: bytes,
    size: int,
) -> bytes:
    with Image.open(BytesIO(content)) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((size, size))
        image = _convert_to_rgb(image)
        output = BytesIO()
        image.save(
            output,
            format="JPEG",
            quality=82,
            optimize=True,
        )
        return output.getvalue()


def _convert_to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new(
            "RGB",
            rgba.size,
            (255, 255, 255),
        )
        background.paste(
            rgba,
            mask=rgba.getchannel("A"),
        )
        return background
    return image.convert("RGB")


def _is_placeholder_thumbnail(
    content: bytes,
    media_type: str,
) -> bool:
    if not media_type.lower().startswith("image/"):
        return False
    return (
        sha256(content).hexdigest()
        in PLACEHOLDER_THUMBNAIL_SHA256
    )


def _cache_key(
    entry: Entry,
    *,
    size: int,
) -> str:
    modified_at = (
        entry.modified_at.isoformat()
        if entry.modified_at is not None
        else "-"
    )
    raw = "|".join(
        (
            str(entry.nas_id),
            entry.full_path,
            str(entry.size_bytes or 0),
            modified_at,
            str(size),
        )
    )
    return sha256(raw.encode("utf-8")).hexdigest()
