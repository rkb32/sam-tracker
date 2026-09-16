"""Thin SAM.gov API client with disk caching.

Personal API keys are rate-limited (roughly 10 calls/day on the opportunities
endpoint), so every network call is cached on disk keyed by its arguments.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
from pathlib import Path

import requests
import urllib3.util.connection as urllib3_conn
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# Some networks have broken IPv6 routing: every connection attempt hangs on an
# unreachable IPv6 address for 10-20s before falling back to IPv4, which works
# fine. Skip straight to IPv4 to avoid that delay.
urllib3_conn.allowed_gai_family = lambda: socket.AF_INET

SEARCH_URL = "https://api.sam.gov/prod/opportunities/v2/search"
DESC_URL = "https://api.sam.gov/prod/opportunities/v1/noticedesc"


class QuotaExceeded(RuntimeError):
    """Personal SAM.gov keys get ~10 calls/day across every endpoint. Cache hard, call rarely."""

    def __init__(self, body: str):
        try:
            when = json.loads(body).get("nextAccessTime", "?")
        except json.JSONDecodeError:
            when = "?"
        super().__init__(f"SAM.gov daily quota exhausted; next access {when}. Re-run with --offline to work from cache.")


class SamClient:
    def __init__(self, api_key: str | None = None, cache_dir: str | Path = "cache"):
        self.api_key = api_key or os.environ.get("SAM_KEY")  # only needed when a call is not cached
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        # Retry transient failures only - never 429, that means quota exhausted
        # (QuotaExceeded below), and retrying it would just burn more of a scarce budget.
        # respect_retry_after_header=False because urllib3 otherwise auto-retries 429
        # too (silently, if the response carries a Retry-After header) even though
        # 429 is deliberately left out of status_forcelist below.
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504], respect_retry_after_header=False)
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    # ---- caching helpers -------------------------------------------------
    def _cache_path(self, kind: str, key: str, ext: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self.cache / f"{kind}_{digest}.{ext}"

    def _get(self, url: str, params: dict, kind: str, *, refresh: bool = False) -> bytes:
        cache_key = url + json.dumps(params, sort_keys=True)
        path = self._cache_path(kind, cache_key, "bin")
        if path.exists() and not refresh:
            return path.read_bytes()
        self._require_key()
        r = self.session.get(url, params={**params, "api_key": self.api_key}, timeout=60)
        if r.status_code == 429:
            raise QuotaExceeded(r.text)
        if not r.ok:
            raise RuntimeError(f"SAM.gov returned {r.status_code} for {url}: {r.text[:300]}")
        path.write_bytes(r.content)
        return r.content

    def _require_key(self) -> None:
        if not self.api_key:
            raise RuntimeError("Set SAM_KEY in the environment (SAM.gov -> Account Details -> Public API Key)")

    # ---- public API ------------------------------------------------------
    def search(self, *, refresh: bool = False, **params) -> list[dict]:
        """Search opportunities. Pass any documented query param (solnum, postedFrom, ...)."""
        params.setdefault("limit", 100)
        raw = self._get(SEARCH_URL, params, "search", refresh=refresh)
        return json.loads(raw)["opportunitiesData"]

    def by_solicitation(self, solnum: str, *, refresh: bool = False) -> list[dict]:
        """Every notice (base + amendments) filed under one solicitation number.

        SAM.gov requires a posted-date window of at most one year, so this covers the
        trailing 365 days. Amendments to notices older than that need a second call.
        """
        from datetime import date, timedelta

        today = date.today()
        window = (today - timedelta(days=364)).strftime("%m/%d/%Y"), today.strftime("%m/%d/%Y")
        return self.search(solnum=solnum, postedFrom=window[0], postedTo=window[1], refresh=refresh)

    def description(self, notice_id: str, *, refresh: bool = False) -> str:
        """Full description text for a notice (the search result only carries a link)."""
        raw = self._get(DESC_URL, {"noticeid": notice_id}, "desc", refresh=refresh)
        try:
            return json.loads(raw).get("description", "")
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace")

    def download(self, url: str, *, refresh: bool = False) -> tuple[bytes, str]:
        """Download an attachment from a resourceLinks URL. Returns (bytes, filename). Cached by URL."""
        path = self._cache_path("file", url, "bin")
        meta = path.with_suffix(".json")
        if path.exists() and meta.exists() and not refresh:
            return path.read_bytes(), json.loads(meta.read_text())["filename"]
        self._require_key()
        r = self.session.get(url, params={"api_key": self.api_key}, timeout=120)
        r.raise_for_status()
        filename = _filename_from_headers(r.headers.get("Content-Disposition", "")) or url.rstrip("/").split("/")[-2]
        path.write_bytes(r.content)
        meta.write_text(json.dumps({"filename": filename, "url": url}))
        return r.content, filename


def _filename_from_headers(content_disposition: str) -> str:
    # e.g. attachment; filename="Amendment 0002.pdf"  or  filename*=UTF-8''Amend%200002.pdf
    import re
    from urllib.parse import unquote

    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", content_disposition)
    if m:
        return unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename="?([^";]+)"?', content_disposition)
    return m.group(1).strip() if m else ""
