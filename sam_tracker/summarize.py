"""Optional LLM summary of a change record. Skipped silently if ANTHROPIC_API_KEY is unset."""
from __future__ import annotations

import json
import os

from .diff import ChangeRecord

SYSTEM = (
    "You summarize amendments to U.S. government solicitations for a contractor deciding "
    "whether their proposal is affected. Be concrete and brief: 2-4 sentences. Lead with the "
    "most consequential change (deadline, scope, set-aside, new attachment). If nothing "
    "material changed, say so in one sentence."
)


def summarize(rec: ChangeRecord) -> str | None:
    if rec.is_empty or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic

    client = anthropic.Anthropic()
    payload = rec.to_dict()
    # Keep the prompt bounded: cap diff lines per text change.
    for tc in payload["text_changes"]:
        tc["added"], tc["removed"] = tc["added"][:60], tc["removed"][:60]
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=1024,
        system=SYSTEM,
        messages=[{"role": "user", "content": "Change record (JSON):\n" + json.dumps(payload, indent=1, default=str)}],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()
