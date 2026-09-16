"""Compare two snapshots and produce a structured change record."""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field, asdict

from .extract import normalize

# Fields whose change should be loud. Everything else in TRACKED_FIELDS still gets reported.
CRITICAL_FIELDS = {"responseDeadLine", "active", "archiveDate", "typeOfSetAside", "naicsCode"}


@dataclass
class FieldChange:
    notice_id: str
    field: str
    before: object
    after: object
    critical: bool = False


@dataclass
class TextChange:
    notice_id: str
    what: str            # "description" or attachment name
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


@dataclass
class AttachmentChange:
    notice_id: str
    kind: str            # added | removed | modified
    name: str
    url: str
    needs_ocr: bool = False
    size_before: int | None = None
    size_after: int | None = None


@dataclass
class ChangeRecord:
    solnum: str
    from_taken_at: str
    to_taken_at: str
    notices_added: list[str] = field(default_factory=list)
    notices_removed: list[str] = field(default_factory=list)
    field_changes: list[FieldChange] = field(default_factory=list)
    attachment_changes: list[AttachmentChange] = field(default_factory=list)
    text_changes: list[TextChange] = field(default_factory=list)
    summary: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.notices_added or self.notices_removed or self.field_changes
                    or self.attachment_changes or self.text_changes)

    @property
    def has_critical(self) -> bool:
        return bool(self.notices_added) or any(f.critical for f in self.field_changes) \
            or any(a.kind != "removed" for a in self.attachment_changes)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["is_empty"] = self.is_empty
        d["has_critical"] = self.has_critical
        return d


def diff_snapshots(old: dict, new: dict) -> ChangeRecord:
    rec = ChangeRecord(solnum=new["solnum"], from_taken_at=old["taken_at"], to_taken_at=new["taken_at"])
    old_n, new_n = old["notices"], new["notices"]

    rec.notices_added = sorted(set(new_n) - set(old_n))
    rec.notices_removed = sorted(set(old_n) - set(new_n))

    for nid in sorted(set(old_n) & set(new_n)):
        a, b = old_n[nid], new_n[nid]
        for key in b:
            if key in ("description", "attachments"):
                continue
            if a.get(key) != b.get(key):
                rec.field_changes.append(FieldChange(nid, key, a.get(key), b.get(key), key in CRITICAL_FIELDS))

        if a.get("description") != b.get("description"):
            tc = _text_diff(nid, "description", a.get("description") or "", b.get("description") or "")
            if tc:
                rec.text_changes.append(tc)

        _diff_attachments(rec, nid, a.get("attachments") or {}, b.get("attachments") or {})

    # A brand-new notice (an amendment posted as its own notice) is reported by id;
    # its full contents are in the snapshot, not repeated here.
    return rec


def _diff_attachments(rec: ChangeRecord, nid: str, old: dict, new: dict) -> None:
    changes: list[AttachmentChange] = []
    for url in sorted(set(new) - set(old)):
        att = new[url]
        changes.append(AttachmentChange(nid, "added", att["name"], url, att["needs_ocr"], None, att["size"]))
    for url in sorted(set(old) - set(new)):
        att = old[url]
        changes.append(AttachmentChange(nid, "removed", att["name"], url, False, att["size"], None))
    for url in sorted(set(old) & set(new)):
        a, b = old[url], new[url]
        if a["sha256"] == b["sha256"]:
            continue
        changes.append(AttachmentChange(nid, "modified", b["name"], url, b["needs_ocr"], a["size"], b["size"]))
        tc = _text_diff(nid, b["name"], a.get("text") or "", b.get("text") or "")
        if tc:
            rec.text_changes.append(tc)

    # Same filename re-uploaded under a new URL: report as modified, not add+remove.
    added = {c.name: c for c in changes if c.kind == "added"}
    removed = {c.name: c for c in changes if c.kind == "removed"}
    for name in added.keys() & removed.keys():
        a_url, b_url = removed[name].url, added[name].url
        if old[a_url]["sha256"] == new[b_url]["sha256"]:
            changes = [c for c in changes if c.url not in (a_url, b_url)]   # identical bytes, just moved
            continue
        changes = [c for c in changes if c.url not in (a_url, b_url)]
        changes.append(AttachmentChange(nid, "modified", name, b_url, new[b_url]["needs_ocr"], old[a_url]["size"], new[b_url]["size"]))
        tc = _text_diff(nid, name, old[a_url].get("text") or "", new[b_url].get("text") or "")
        if tc:
            rec.text_changes.append(tc)

    rec.attachment_changes.extend(changes)


def _text_diff(nid: str, what: str, old: str, new: str) -> TextChange | None:
    a, b = normalize(old), normalize(new)
    if a == b:
        return None
    tc = TextChange(nid, what)
    for line in difflib.unified_diff(a, b, lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            tc.added.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            tc.removed.append(line[1:])
    return tc


def render(rec: ChangeRecord, *, max_lines: int = 20) -> str:
    """Human-readable report."""
    out = [f"{rec.solnum}: {rec.from_taken_at} -> {rec.to_taken_at}"]
    if rec.is_empty:
        out.append("  no changes")
        return "\n".join(out)
    for nid in rec.notices_added:
        out.append(f"  + NEW NOTICE {nid}")
    for nid in rec.notices_removed:
        out.append(f"  - notice gone {nid}")
    for fc in rec.field_changes:
        flag = "!" if fc.critical else " "
        out.append(f"  {flag} {fc.field}: {fc.before!r} -> {fc.after!r}  [{fc.notice_id}]")
    for ac in rec.attachment_changes:
        extra = " (scanned PDF - needs OCR)" if ac.needs_ocr else ""
        size = f" {ac.size_before}->{ac.size_after}B" if ac.kind == "modified" else ""
        out.append(f"  * attachment {ac.kind}: {ac.name}{size}{extra}  [{ac.notice_id}]")
    for tc in rec.text_changes:
        out.append(f"  ~ {tc.what}: +{len(tc.added)} / -{len(tc.removed)} lines  [{tc.notice_id}]")
        for ln in tc.removed[:max_lines]:
            out.append(f"      - {ln[:160]}")
        for ln in tc.added[:max_lines]:
            out.append(f"      + {ln[:160]}")
        if len(tc.added) + len(tc.removed) > 2 * max_lines:
            out.append("      ...")
    if rec.summary:
        out.append("  summary: " + rec.summary.replace("\n", "\n           "))
    return "\n".join(out)
