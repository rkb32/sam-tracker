# sam-tracker

A government contractor tracking a solicitation on SAM.gov gets a "something
changed" email but has to click through and eyeball the whole notice again to
find *what*. This does that diff for you: snapshot a solicitation, run it again
later, and it prints exactly what moved.

```
$ sam-tracker check
36C25226Q0629  (checked 2026-09-14T12:00:00+00:00 -> 2026-09-16T03:54:58+00:00)

NEW NOTICE(S) POSTED:
  - fbe02225c2f7450d9d49be1201f2101e

FIELDS CHANGED:
  responseDeadLine: [IMPORTANT]
    was: '2026-09-19T09:00:00-05:00'
    now: '2026-09-23T09:00:00-05:00'

ATTACHMENTS:
  36C25226Q0629_1.docx - modified (23657B -> 23657B)

WHAT CHANGED INSIDE THE DOCUMENTS:
  36C25226Q0629_1.docx:
    removed: HVAC Boiler Preventative Maintenance Services
    added:   HVAC Chiller Preventative Maintenance Services
```

Exit code `2` when anything changed, so cron can alert on it.

## Run it

```bash
python -m venv .venv && .venv/Scripts/activate      # or source .venv/bin/activate
pip install -e ".[llm]"
export SAM_KEY=...                                  # sam.gov -> Account Details -> Public API Key
sam-tracker track 36C25226Q0629                     # first snapshot
sam-tracker check                                   # later: re-snapshot everything tracked, print diffs
sam-tracker history 36C25226Q0629
```

Optional: `export ANTHROPIC_API_KEY=...` and `check` appends a 2-4 sentence plain-English
summary of each change record (Claude Opus 5). Skipped silently when unset.

Storage is SQLite (`tracker.db`) by default. `export DATABASE_URL=postgresql://...`
(and `pip install -e ".[postgres]"`) to use Postgres - same SQLAlchemy models.

## How it works

```
client.py    SAM.gov API + disk cache. Every response is cached by URL+params.
snapshot.py  solnum -> {notices: {noticeId: {fields, description, attachments{url: {sha256, text}}}}}
extract.py   PDF (pypdf) and DOCX text. Flags scanned PDFs as needs_ocr instead of pretending.
diff.py      snapshot A vs B -> ChangeRecord (field / attachment / text changes). Pure, unit-tested.
db.py        tracked / snapshots / changes tables (SQLAlchemy 2.0).
summarize.py optional LLM summary of a ChangeRecord.
cli.py       track / check / history / list
```

`track` takes the first snapshot of a solicitation. `check` re-snapshots it and
diffs against the last one. Under the hood, `check` re-downloads every notice
and attachment (PDF/DOCX text is extracted so the diff can compare content, not
just bytes), hashes the result, and if anything changed it runs `diff.py`
against the previous snapshot and prints the result.

Two things worth knowing if you're reading the code:

- **Attachments are matched by content hash, not URL.** SAM.gov re-uploads a
  revised document under a brand-new URL, so matching by hash lets a same-name
  re-upload show up as *modified* instead of a confusing add+remove.
- **The SAM.gov API key is the bottleneck**, not the code. A personal key gets
  ~10 calls/day total. `--offline` replays cached responses so you can iterate
  without spending calls; a `429` exits with code `3` and prints the reset time.

## Scheduling

Cron, once a day:

```
0 13 * * *  cd /opt/sam-tracker && SAM_KEY=... .venv/bin/sam-tracker check --json >> changes.jsonl
```

Dagster: `examples/dagster_defs.py` wraps `check` as a daily asset (untested against a live
Dagster install - it is the obvious 20 lines).

## Tests

```bash
pip install -e ".[dev]" && pytest
```

Covers the diff engine: identical snapshots, critical vs non-critical fields, whitespace-only
description changes, new-notice amendments, attachment add/modify/remove, same-name re-uploads.

## Known gaps

- OCR: scanned PDFs are flagged (`needs_ocr`) but not read. Tesseract would slot into `extract.py`.
- The search window is the trailing 364 days (SAM.gov caps it at one year). Amendments to a
  notice older than that need a second window.
- `solicitationNumber` on SAM.gov sometimes carries trailing whitespace; `track` uses the value
  as typed.
