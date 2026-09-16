# sam-tracker

Tracks amendments to SAM.gov solicitations. Snapshots everything filed under a
solicitation number (every notice, its description, every attachment), stores it,
and on the next run reports exactly what changed: new amendment notices, deadline
moves, set-aside/NAICS changes, added or re-uploaded attachments, and line-level
text diffs of the documents themselves.

SAM.gov's own "follow" emails tell you *that* something changed. This tells you *what*.

```
$ sam-tracker check
36C25226Q0629: 2026-09-14T12:00:00+00:00 -> 2026-09-16T03:54:58+00:00
  + NEW NOTICE fbe02225c2f7450d9d49be1201f2101e
  ! responseDeadLine: '2026-09-19T09:00:00-05:00' -> '2026-09-23T09:00:00-05:00'  [37d1ff14...]
  * attachment modified: 36C25226Q0629_1.docx 23657->23657B  [37d1ff14...]
  ~ 36C25226Q0629_1.docx: +6 / -5 lines  [37d1ff14...]
      - HVAC Boiler Preventative Maintenance Services
      + HVAC Chiller Preventative Maintenance Services
```

`!` = critical field. Exit code `2` when anything changed, so cron/CI can alert on it.

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

Design choices worth knowing:

- **Snapshot-diff, not event-driven.** SAM.gov has no changelog API and agencies post
  amendments inconsistently (sometimes a new notice, sometimes an edit to the existing one).
  Diffing full snapshots catches both without special-casing.
- **Attachments are compared by content hash, then by extracted text.** A re-upload under a
  new URL with the same filename is reported as *modified*, not add+remove. Identical bytes at a
  new URL are ignored.
- **DOCX extraction walks the XML directly.** `python-docx`'s `paragraphs` skips text boxes,
  and SF-30 amendment forms are almost entirely text boxes - the first real amendment I pulled
  extracted 0 characters that way.
- **Quota is the real constraint.** A personal SAM.gov key gets ~10 calls/day *across all
  endpoints* (search, description, file download each count). `check` costs `1 + notices` calls
  per solicitation plus one per new attachment. `--offline` replays the cache so you can develop
  without spending calls; a 429 exits with code 3 and the reset time.

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
