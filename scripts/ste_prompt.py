"""Render the STE100 rewriter system prompt.

    python scripts/ste_prompt.py [--limit-instruction 20] [--limit-description 25] [--tokens]

Fills ste/rewrite-prompt.md (our own words, committed) with the approved and
unapproved verbs from var/ste/approved-verbs.json (scripts/ste_extract.py; text from the
standard, local only) and the technical verbs in ste/technical-verbs.toml. Writes
var/ste/prompt-<sha12>.json: the system prompt, the worked examples as turns, and
every input's sha256 and the sentence limits, so a conversion run can name the exact prompt it used.

The sentence limits are owed by the operator (20 for all emissions, or 20 for
instructions and 25 for descriptions); the defaults are the standard's rules 5.1 and 6.3.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "ste" / "rewrite-prompt.md"
TECHNICAL = REPO / "ste" / "technical-verbs.toml"
EXAMPLES = REPO / "ste" / "rewrite-examples.md"
VERBS = REPO / "var" / "ste" / "approved-verbs.json"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def approved_lines(verbs: list[dict]) -> str:
    out = []
    for v in verbs:
        forms = ", ".join(f for f in v["forms"] if f != v["key"])
        head = f"{v['key']} ({forms})" if forms else v["key"]
        ms = v["meanings"]
        mean = ms[0] if len(ms) == 1 else " ".join(f"{i}. {m}" for i, m in enumerate(ms, 1))
        out.append(f"- {head}: {mean}")
    return "\n".join(out)


def technical_lines(tech: dict[str, str]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in tech.items())


def unapproved_lines(unapproved: list[dict]) -> str:
    out = []
    for u in unapproved:
        alts = ", ".join(u["alternatives"]) if u["alternatives"] else "(write the sentence in a different way)"
        out.append(f"- {u['word']}: {alts}")
    return "\n".join(out)


def wrap(text: str) -> str:
    """The user turn: one text between the markers the system prompt names."""
    return f"<<<TEXT\n{text}\nTEXT>>>"


def examples() -> list[dict]:
    """Worked examples as alternating user and assistant turns."""
    msgs = []
    for block in EXAMPLES.read_text().split("\n---\n")[1:]:
        m = re.match(r"\s*## Input\n\n(.*?)\n\n## Output\n\n(.*?)\s*$", block, re.S)
        if not m:
            raise SystemExit(f"bad example block: {block[:60]!r}")
        msgs += [{"role": "user", "content": wrap(m.group(1))}, {"role": "assistant", "content": m.group(2)}]
    return msgs


def render(limit_instruction: int, limit_description: int) -> tuple[str, dict]:
    tpl = TEMPLATE.read_bytes()
    tech_b = TECHNICAL.read_bytes()
    verbs_b = VERBS.read_bytes()
    verbs = json.loads(verbs_b)
    tech = tomllib.loads(tech_b.decode())["verbs"]
    text = (tpl.decode()
            .replace("{{LIMIT_INSTRUCTION}}", str(limit_instruction))
            .replace("{{LIMIT_DESCRIPTION}}", str(limit_description))
            .replace("{{APPROVED_VERBS}}", approved_lines(verbs["verbs"]))
            .replace("{{TECHNICAL_VERBS}}", technical_lines(tech))
            .replace("{{UNAPPROVED_VERBS}}", unapproved_lines(verbs["unapproved_verbs"])))
    if "{{" in text:
        raise SystemExit("unfilled slot in the template")
    meta = {
        "prompt_sha256": sha(text.encode()),
        "template_sha256": sha(tpl), "technical_verbs_sha256": sha(tech_b),
        "examples_sha256": sha(EXAMPLES.read_bytes()),
        "verbs_sha256": sha(verbs_b), "ste100_source_sha256": verbs["source_sha256"],
        "limit_instruction": limit_instruction, "limit_description": limit_description,
        "counts": {"approved_verbs": len(verbs["verbs"]), "technical_verbs": len(tech),
                   "unapproved_verbs": len(verbs["unapproved_verbs"])},
    }
    return text, meta


def count_tokens(text: str) -> int:
    from transformers import AutoTokenizer

    spec = tomllib.loads((REPO / "config" / "models.toml").read_text())["models"]["nemotron-3-nano-30b-a3b"]
    tok = AutoTokenizer.from_pretrained(Path(os.environ["WEAVER_TRAIN_MODEL_STORE"]) / spec["local_dir"])
    return len(tok(text)["input_ids"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit-instruction", type=int, default=20)
    ap.add_argument("--limit-description", type=int, default=25)
    ap.add_argument("--tokens", action="store_true", help="count tokens with the model's tokenizer")
    a = ap.parse_args(argv)
    text, meta = render(a.limit_instruction, a.limit_description)
    if a.tokens:
        meta["tokens"] = count_tokens(text + "".join(m["content"] for m in examples()))
    shots = examples()
    meta["prompt_sha256"] = sha(json.dumps([text, shots]).encode())
    out = VERBS.parent / f"prompt-{meta['prompt_sha256'][:12]}.json"
    out.write_text(json.dumps({"meta": meta, "system": text, "examples": shots}, indent=1) + "\n")
    print(json.dumps(meta, indent=1))
    print(f"prompt: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
