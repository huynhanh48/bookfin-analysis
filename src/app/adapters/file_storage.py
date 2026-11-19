"""Persistent file boundary, replaceable by S3/MinIO without changing services."""

import asyncio
import base64
import hashlib
import io
import warnings
from pathlib import Path
from typing import Protocol

from fastapi import UploadFile
from PIL import Image, UnidentifiedImageError

from app.core.config import Settings
from app.core.exceptions import InvalidRequestError


class FileStorage(Protocol):
    """Store validated original bytes and retrieve them by opaque key."""

    async def save(self, data: bytes, suffix: str) -> str:
        """Persist bytes and return a durable reference."""
        ...

    async def read(self, key: str) -> bytes:
        """Read original bytes without exposing filesystem paths to clients."""
        ...

    def path(self, key: str) -> Path:
        """Materialize a library-readable path (remote storage may stage locally)."""
        ...


class LocalFileStorage:
    """Content-addressed original files on a persistent mounted directory."""

    def __init__(self, directory: str):
        self.directory = Path(directory).resolve()

    def path(self, key: str) -> Path:
        """Reject traversal before resolving an opaque stored key."""
        if Path(key).name != key:
            raise InvalidRequestError("Invalid file reference.")
        return self.directory / key

    async def save(self, data: bytes, suffix: str) -> str:
        """Atomic write off the event loop; identical bytes reuse a key."""
        key = hashlib.sha256(data).hexdigest() + suffix

        def write() -> None:
            self.directory.mkdir(parents=True, exist_ok=True)
            import os
            import tempfile

            fd, name = tempfile.mkstemp(dir=self.directory)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                os.replace(name, self.path(key))
            finally:
                if os.path.exists(name):
                    os.unlink(name)

        await asyncio.to_thread(write)
        return key

    async def read(self, key: str) -> bytes:
        """Read from persistent disk without blocking FastAPI."""
        return await asyncio.to_thread(self.path(key).read_bytes)


async def validate_upload(
    upload: UploadFile, settings: Settings, image_only: bool = False
) -> tuple[bytes, str]:
    """Bound read and decode image headers; never trust extension or MIME alone."""
    data = await upload.read(settings.rag_max_upload_bytes + 1)
    if not data or len(data) > settings.rag_max_upload_bytes:
        raise InvalidRequestError("File is empty or exceeds the upload limit.")
    if not image_only and upload.content_type == "application/pdf" and data.startswith(b"%PDF-"):
        return data, ".pdf"

    def inspect() -> str:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if image.width * image.height > settings.rag_max_image_pixels:
                        raise InvalidRequestError("Image exceeds the pixel limit.")
                    fmt = image.format
                    if fmt not in {"PNG", "JPEG", "WEBP"} or upload.content_type != Image.MIME[fmt]:
                        raise InvalidRequestError("Unsupported image type or MIME mismatch.")
                    image.verify()
                    return {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[fmt]
        except (
            UnidentifiedImageError,
            OSError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as exc:
            raise InvalidRequestError("Invalid image.") from exc

    return data, await asyncio.to_thread(inspect)


async def image_data_url(storage: FileStorage, key: str) -> str:
    """Encode an already validated local chart only for the provider request."""
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}[Path(key).suffix]
    return f"data:{mime};base64," + base64.b64encode(await storage.read(key)).decode()


async def validate_query_image(data_url: str, settings: Settings) -> str:
    """Validate an in-memory query image without saving it or creating ingestion jobs."""
    from starlette.datastructures import Headers

    try:
        header, encoded = data_url.split(",", 1)
        if header not in (
            "data:image/png;base64",
            "data:image/jpeg;base64",
            "data:image/webp;base64",
        ):
            raise ValueError("Unsupported image type")
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise InvalidRequestError("Invalid query image data URL.") from exc
    upload = UploadFile(
        file=io.BytesIO(data), filename="query", headers=Headers({"content-type": header[5:-7]})
    )
    try:
        await validate_upload(upload, settings, image_only=True)
    finally:
        await upload.close()
    return data_url
