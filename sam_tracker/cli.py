"""Command line entry point.

  sam-tracker track <solnum> [...]   start tracking (takes the first snapshot)
  sam-tracker check [<solnum> ...]   re-snapshot tracked solicitations and report changes
  sam-tracker history <solnum>       list stored changes
  sam-tracker list                   tracked solicitations
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from .client import QuotaExceeded, SamClient
from .db import Change, Snapshot, Tracked, get_engine, init_db, latest_snapshot
from .diff import ChangeRecord, diff_snapshots, render
from .snapshot import content_hash, take_snapshot
from .summarize import summarize


def cmd_track(args, session: Session, client: SamClient) -> int:
    for solnum in args.solnum:
        if session.get(Tracked, solnum):
            print(f"{solnum}: already tracked")
            continue
        snap = take_snapshot(client, solnum, refresh=not args.offline, with_attachments=not args.no_attachments)
        if not snap["notices"]:
            print(f"{solnum}: no notices found on SAM.gov (check the solicitation number)")
            continue
        session.add(Tracked(solnum=solnum))
        session.add(Snapshot(solnum=solnum, taken_at=snap["taken_at"], content_hash=content_hash(snap), payload=snap))
        session.commit()
        n_att = sum(len(n["attachments"]) for n in snap["notices"].values())
        print(f"{solnum}: tracking {len(snap['notices'])} notice(s), {n_att} attachment(s)")
    return 0


def cmd_check(args, session: Session, client: SamClient) -> int:
    solnums = args.solnum or [t.solnum for t in session.scalars(select(Tracked))]
    if not solnums:
        print("nothing tracked yet; run: sam-tracker track <solnum>")
        return 1
    any_change = False
    for solnum in solnums:
        prev = latest_snapshot(session, solnum)
        if prev is None:
            print(f"{solnum}: not tracked; run `track` first")
            continue
        snap = take_snapshot(client, solnum, refresh=not args.offline, with_attachments=not args.no_attachments)
        h = content_hash(snap)
        if h == prev.content_hash:
            print(f"{solnum}: no changes")
            continue
        cur = Snapshot(solnum=solnum, taken_at=snap["taken_at"], content_hash=h, payload=snap)
        session.add(cur)
        session.flush()
        rec = diff_snapshots(prev.payload, snap)
        if rec.is_empty:
            # Bytes differed (e.g. contact block reordered, extractor upgraded) but nothing we track moved.
            session.commit()
            print(f"{solnum}: no material changes")
            continue
        if not args.no_summary:
            rec.summary = summarize(rec)
        session.add(Change(solnum=solnum, from_snapshot_id=prev.id, to_snapshot_id=cur.id,
                           critical=rec.has_critical, record=rec.to_dict(), summary=rec.summary))
        session.commit()
        any_change = True
        print(json.dumps(rec.to_dict(), default=str) if args.json else render(rec))
    return 2 if any_change else 0   # exit code 2 = changes found (handy for cron/alerts)


def cmd_history(args, session: Session, client: SamClient) -> int:
    stmt = select(Change).where(Change.solnum == args.solnum).order_by(Change.id)
    rows = list(session.scalars(stmt))
    if not rows:
        print(f"{args.solnum}: no changes recorded")
        return 0
    for ch in rows:
        if args.json:
            print(json.dumps(ch.record, default=str))
        else:
            rec = ChangeRecord(**{k: v for k, v in ch.record.items() if k not in ("is_empty", "has_critical")})
            print(render(_rehydrate(rec)))
            print()
    return 0


def cmd_list(args, session: Session, client: SamClient) -> int:
    for t in session.scalars(select(Tracked)):
        last = latest_snapshot(session, t.solnum)
        n_changes = len(list(session.scalars(select(Change).where(Change.solnum == t.solnum))))
        print(f"{t.solnum}\tlast snapshot {last.taken_at if last else '-'}\t{n_changes} change(s)")
    return 0


def _rehydrate(rec: ChangeRecord) -> ChangeRecord:
    """JSON round-trip turns dataclasses into dicts; put them back for render()."""
    from .diff import AttachmentChange, FieldChange, TextChange

    rec.field_changes = [FieldChange(**f) for f in rec.field_changes]
    rec.attachment_changes = [AttachmentChange(**a) for a in rec.attachment_changes]
    rec.text_changes = [TextChange(**t) for t in rec.text_changes]
    return rec


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sam-tracker", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="SQLAlchemy URL (default: $DATABASE_URL or sqlite:///tracker.db)")
    p.add_argument("--cache", default="cache", help="directory for cached API responses and attachments")
    p.add_argument("--offline", action="store_true", help="never call SAM.gov; replay cached responses (testing / quota exhausted)")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("track", help="start tracking solicitation number(s)")
    t.add_argument("solnum", nargs="+")
    t.add_argument("--no-attachments", action="store_true")
    t.set_defaults(fn=cmd_track)

    c = sub.add_parser("check", help="re-snapshot and report changes")
    c.add_argument("solnum", nargs="*")
    c.add_argument("--no-attachments", action="store_true")
    c.add_argument("--no-summary", action="store_true", help="skip the LLM summary even if ANTHROPIC_API_KEY is set")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_check)

    h = sub.add_parser("history", help="show recorded changes")
    h.add_argument("solnum")
    h.add_argument("--json", action="store_true")
    h.set_defaults(fn=cmd_history)

    sub.add_parser("list", help="tracked solicitations").set_defaults(fn=cmd_list)

    args = p.parse_args(argv)
    engine = get_engine(args.db)
    init_db(engine)
    client = SamClient(cache_dir=args.cache) if args.cmd in ("track", "check") else None
    with Session(engine) as session:
        try:
            return args.fn(args, session, client)
        except QuotaExceeded as e:
            print(f"error: {e}", file=sys.stderr)
            return 3


if __name__ == "__main__":
    sys.exit(main())
