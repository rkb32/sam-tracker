"""Build a point-in-time snapshot of everything filed under a solicitation number."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .client import SamClient
from .extract import extract_text

# Notice fields worth tracking. Anything else in the API response is ignored.
TRACKED_FIELDS = (
    "title", "type", "baseType", "postedDate", "responseDeadLine", "active",
    "archiveDate", "archiveType", "typeOfSetAside", "typeOfSetAsideDescription",
    "naicsCode", "classificationCode", "fullParentPathName", "uiLink",
)


def take_snapshot(client: SamClient, solnum: str, *, refresh: bool = True, with_attachments: bool = True) -> dict:
    notices = {}
    for raw in client.by_solicitation(solnum, refresh=refresh):
        nid = raw["noticeId"]
        notice = {k: raw.get(k) for k in TRACKED_FIELDS}
        notice["pointOfContact"] = [
            {k: c.get(k) for k in ("type", "fullName", "email", "phone")} for c in (raw.get("pointOfContact") or [])
        ]
        notice["placeOfPerformance"] = raw.get("placeOfPerformance")
        notice["description"] = client.description(nid, refresh=refresh)
        notice["attachments"] = {}
        if with_attachments:
            for url in raw.get("resourceLinks") or []:
                data, filename = client.download(url, refresh=False)  # content at a URL is immutable; new versions get new URLs
                text, needs_ocr = extract_text(data, filename)
                notice["attachments"][url] = {
                    "name": filename,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                    "text": text,
                    "needs_ocr": needs_ocr,
                }
        notices[nid] = notice
    return {
        "solnum": solnum,
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "notices": notices,
    }


def content_hash(snapshot: dict) -> str:
    """Stable hash of the parts of a snapshot that matter (ignores taken_at)."""
    import json

    payload = json.dumps(snapshot["notices"], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()
