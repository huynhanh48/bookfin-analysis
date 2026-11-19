"""Tokenizer bootstrap must bound failures and avoid repeated network downloads."""

import hashlib

import httpx
import pytest

from app.adapters import rag_bootstrap
from app.core.exceptions import UpstreamError


async def test_cache_verified_and_reused(tmp_path, monkeypatch, respx_mock):
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    content = b"test tokenizer fixture"
    monkeypatch.setattr(rag_bootstrap, "SHA256", hashlib.sha256(content).hexdigest())
    route = respx_mock.get(rag_bootstrap.URL).mock(
        return_value=httpx.Response(200, content=content)
    )
    await rag_bootstrap.prepare_tokenizer(tmp_path)
    await rag_bootstrap.prepare_tokenizer(tmp_path)
    assert route.call_count == 1


async def test_seeded_cache_is_copied_without_network(tmp_path, monkeypatch, respx_mock):
    seeded = tmp_path / "seed"
    seeded.mkdir()
    content = b"pre-seeded tokenizer fixture"
    monkeypatch.setattr(rag_bootstrap, "SHA256", hashlib.sha256(content).hexdigest())
    name = hashlib.sha1(rag_bootstrap.URL.encode()).hexdigest()
    (seeded / name).write_bytes(content)
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(seeded))
    route = respx_mock.get(rag_bootstrap.URL).mock(
        return_value=httpx.Response(200, content=b"must not download")
    )
    target = tmp_path / "target"
    await rag_bootstrap.prepare_tokenizer(target)
    assert (target / name).read_bytes() == content
    assert route.call_count == 0


async def test_download_failure_is_safe(tmp_path, respx_mock):
    respx_mock.get(rag_bootstrap.URL).mock(side_effect=httpx.ReadTimeout("private detail"))
    with pytest.raises(UpstreamError, match="Tokenizer download failed"):
        await rag_bootstrap.prepare_tokenizer(tmp_path)


async def test_checksum_rejects_corrupt_download(tmp_path, respx_mock):
    respx_mock.get(rag_bootstrap.URL).mock(return_value=httpx.Response(200, content=b"corrupt"))
    with pytest.raises(UpstreamError, match="checksum"):
        await rag_bootstrap.prepare_tokenizer(tmp_path)
