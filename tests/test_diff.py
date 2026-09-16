import copy
import hashlib

from sam_tracker.diff import diff_snapshots, render


def _att(text: str, name="Attachment 1.pdf", needs_ocr=False):
    data = text.encode()
    return {"name": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "text": text, "needs_ocr": needs_ocr}


def _snapshot():
    return {
        "solnum": "W912DQ-26-R-0001",
        "taken_at": "2026-09-14T00:00:00+00:00",
        "notices": {
            "n1": {
                "title": "Display unit repair",
                "type": "Solicitation",
                "responseDeadLine": "2026-09-30T17:00:00-04:00",
                "active": "Yes",
                "typeOfSetAside": "SBA",
                "naicsCode": "336413",
                "description": "Contractor shall repair units.\nDelivery within 30 days.",
                "attachments": {"https://x/files/a/download": _att("Section L\nSection M")},
            }
        },
    }


def test_identical_snapshots_are_empty():
    a, b = _snapshot(), _snapshot()
    b["taken_at"] = "2026-09-15T00:00:00+00:00"
    rec = diff_snapshots(a, b)
    assert rec.is_empty
    assert "no changes" in render(rec)


def test_deadline_change_is_critical():
    a, b = _snapshot(), _snapshot()
    b["notices"]["n1"]["responseDeadLine"] = "2026-10-07T17:00:00-04:00"
    rec = diff_snapshots(a, b)
    assert len(rec.field_changes) == 1
    fc = rec.field_changes[0]
    assert fc.field == "responseDeadLine" and fc.critical
    assert rec.has_critical


def test_non_critical_field_change():
    a, b = _snapshot(), _snapshot()
    b["notices"]["n1"]["title"] = "Display unit repair (updated)"
    rec = diff_snapshots(a, b)
    assert [f.field for f in rec.field_changes] == ["title"]
    assert not rec.has_critical


def test_description_diff_ignores_whitespace():
    a, b = _snapshot(), _snapshot()
    b["notices"]["n1"]["description"] = "Contractor  shall repair   units.\n\nDelivery within 30 days.\n"
    assert diff_snapshots(a, b).text_changes == []
    b["notices"]["n1"]["description"] = "Contractor shall repair units.\nDelivery within 45 days."
    rec = diff_snapshots(a, b)
    assert rec.text_changes[0].removed == ["Delivery within 30 days."]
    assert rec.text_changes[0].added == ["Delivery within 45 days."]


def test_new_notice_is_amendment():
    a, b = _snapshot(), _snapshot()
    b["notices"]["n2"] = copy.deepcopy(b["notices"]["n1"]) | {"title": "Amendment 0001"}
    rec = diff_snapshots(a, b)
    assert rec.notices_added == ["n2"] and rec.has_critical


def test_attachment_added_modified_removed():
    a, b = _snapshot(), _snapshot()
    n = b["notices"]["n1"]
    n["attachments"]["https://x/files/a/download"] = _att("Section L\nSection M (revised)")
    n["attachments"]["https://x/files/b/download"] = _att("Q&A", name="QA.pdf", needs_ocr=True)
    rec = diff_snapshots(a, b)
    kinds = {(c.kind, c.name) for c in rec.attachment_changes}
    assert kinds == {("modified", "Attachment 1.pdf"), ("added", "QA.pdf")}
    assert any(c.needs_ocr for c in rec.attachment_changes if c.name == "QA.pdf")
    assert rec.text_changes[0].what == "Attachment 1.pdf"
    assert rec.text_changes[0].added == ["Section M (revised)"]

    c = _snapshot()
    c["notices"]["n1"]["attachments"] = {}
    rec = diff_snapshots(a, c)
    assert [x.kind for x in rec.attachment_changes] == ["removed"]
    assert not rec.has_critical


def test_same_name_new_url_is_modified_not_add_remove():
    a, b = _snapshot(), _snapshot()
    b["notices"]["n1"]["attachments"] = {"https://x/files/zzz/download": _att("Section L\nSection M v2")}
    rec = diff_snapshots(a, b)
    assert [(c.kind, c.name) for c in rec.attachment_changes] == [("modified", "Attachment 1.pdf")]

    # identical bytes at a new URL: nothing to report
    b["notices"]["n1"]["attachments"] = {"https://x/files/zzz/download": _att("Section L\nSection M")}
    assert diff_snapshots(a, b).is_empty


def test_to_dict_round_trips_json():
    import json

    a, b = _snapshot(), _snapshot()
    b["notices"]["n1"]["active"] = "No"
    d = diff_snapshots(a, b).to_dict()
    json.dumps(d)
    assert d["has_critical"] is True and d["is_empty"] is False


def test_summarize_is_skipped_without_key(monkeypatch):
    from sam_tracker.summarize import summarize

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    a, b = _snapshot(), _snapshot()
    b["notices"]["n1"]["active"] = "No"
    assert summarize(diff_snapshots(a, b)) is None
