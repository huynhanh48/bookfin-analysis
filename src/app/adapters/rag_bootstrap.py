"""Bounded tokenizer bootstrap; never block the API loop on library downloads."""

import asyncio
import hashlib
import os
from pathlib import Path

import httpx

from app.core.exceptions import UpstreamError

URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"

# The vocabulary is small (~3.6 MB) but provider/CDN throughput varies. A per-read
# 20s timeout is too tight on slow links; keep a short connect bound and a generous
# overall read bound, with one retry for transient network failures.
CONNECT_TIMEOUT_SECONDS = 20.0
DOWNLOAD_TIMEOUT_SECONDS = 120.0
DOWNLOAD_ATTEMPTS = 2


def _cache_dir() -> Path:
    """Return the persistent tokenizer directory configured for the process."""
    return Path(os.environ.get("TIKTOKEN_CACHE_DIR", "."))


def _shared_cache_dir() -> Path | None:
    """Return an optional shared cache that survives per-workspace temp dirs."""
    value = os.environ.get("RAG_TOKENIZER_CACHE_DIR")
    return Path(value) if value else None


async def _valid_cache(target: Path) -> bool:
    """Check an existing vocabulary file without trusting a partial download."""
    if not target.exists():
        return False
    try:
        digest = hashlib.sha256(await asyncio.to_thread(target.read_bytes)).hexdigest()
    except OSError:
        return False
    return digest == SHA256


async def prepare_tokenizer(directory: Path) -> None:
    """Persist the verified tokenizer vocabulary with bounded network time.

    An already-valid cache (including one seeded via ``TIKTOKEN_CACHE_DIR``) is
    reused without network access, which keeps offline/CI boots deterministic.
    Download failures raise a retryable domain error instead of hanging the worker.

    Args:
        directory: Persistent directory for the tokenizer vocabulary.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / hashlib.sha1(URL.encode()).hexdigest()
    if await _valid_cache(target):
        os.environ["TIKTOKEN_CACHE_DIR"] = str(directory)
        return

    shared = _shared_cache_dir()
    for seeded in (_cache_dir(), *([shared] if shared else [])):
        seeded_file = seeded / target.name
        if seeded == directory or not await _valid_cache(seeded_file):
            continue
        content = await asyncio.to_thread(seeded_file.read_bytes)
        await asyncio.to_thread(target.write_bytes, content)
        os.environ["TIKTOKEN_CACHE_DIR"] = str(directory)
        return

    last_error: BaseException | None = None
    for _attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            timeout = httpx.Timeout(DOWNLOAD_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)
            async with (
                asyncio.timeout(DOWNLOAD_TIMEOUT_SECONDS + 10),
                httpx.AsyncClient(timeout=timeout) as client,
            ):
                response = await client.get(URL)
                response.raise_for_status()
                content = response.content
            if hashlib.sha256(content).hexdigest() != SHA256:
                raise UpstreamError("Tokenizer checksum mismatch.")
            await asyncio.to_thread(target.write_bytes, content)
            os.environ["TIKTOKEN_CACHE_DIR"] = str(directory)
            return
        except UpstreamError:
            raise
        except (httpx.HTTPError, TimeoutError) as exc:
            last_error = exc

    raise UpstreamError("Tokenizer download failed; retry ingestion later.") from last_error
