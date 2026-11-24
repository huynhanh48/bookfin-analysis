"""News provider boundaries and conservative URL/time normalization."""

import ipaddress
from datetime import UTC, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class NewsEvidenceProvider(Protocol):
    """Search and extraction are separate to bound fetches to relevant candidates."""

    async def search(
        self,
        query: str,
        analysis_at: datetime,
        *,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[dict[str, Any]]: ...
    async def extract(self, url: str) -> dict[str, Any]: ...


def canonical_url(url: str) -> str:
    """Deduplicate tracking variants and reject nonpublic or credential-bearing URLs."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
        raise ValueError("Invalid source URL")
    if host == "localhost" or host.endswith((".local", ".internal")) or "." not in host:
        raise ValueError("Nonpublic source URL")
    if host in ("news.google.com", "google.com", "www.google.com") and parsed.path.startswith(
        ("/url", "/rss/articles")
    ):
        raise ValueError("Search redirect URL")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValueError("Nonpublic source IP")
    except ValueError as exc:
        if str(exc) == "Nonpublic source IP":
            raise
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query)
        if not k.startswith("utm_") and k not in ("fbclid", "gclid")
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc.lower(), parsed.path or "/", urlencode(sorted(query)), "")
    )


def publication_time(value: Any) -> datetime | None:
    """Reject unknown/naive timestamps; date-only sources use a conservative latest-world-time bound."""
    if not value:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        try:
            if len(str(value)) == 10:
                return datetime.combine(
                    datetime.strptime(str(value), "%Y-%m-%d").date(),
                    time.max,
                    timezone(timedelta(hours=-12)),
                )
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            try:
                result = parsedate_to_datetime(str(value))
            except (ValueError, TypeError):
                return None
    return result.astimezone(UTC) if result.tzinfo and result.utcoffset() is not None else None
