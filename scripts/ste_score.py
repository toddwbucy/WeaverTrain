"""Score STE100 rewrites made outside the pilot (e.g. by an opencode agent).

    python scripts/ste_score.py DIR/output.jsonl [--input DIR/input.jsonl] [--md DIR/score.md]

input.jsonl holds {"id", "kind", "text"} (code masked as [[Cn]]); output.jsonl holds
{"id", "output"}. Each rewrite gets the pilot's checks: scripts/ste_check.py's hard
rules plus the converter's guards (no copied example line, word ratio 0.5 to 2.5).
Prints pass rates by kind and the failing rules, and writes a side-by-side file.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "src")]
import ste_check  # noqa: E402
import ste_convert  # noqa: E402


def newest_prompt() -> Path:
    return max((REPO / "var" / "ste").glob("prompt-*.json"), key=lambda p: p.stat().st_mtime)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("output")
    ap.add_argument("--input")
    ap.add_argument("--md")
    ap.add_argument("--prompt", help="var/ste/prompt-<sha12>.json (limits, examples); default newest")
    a = ap.parse_args(argv)
    out_path = Path(a.output)
    if a.input:
        in_path = Path(a.input)
    else:  # the nearest input.jsonl above the output (kit/runs/<model>/output.jsonl)
        in_path = next((d / "input.jsonl" for d in out_path.resolve().parents
                        if (d / "input.jsonl").exists()), None)
        if in_path is None:
            ap.error("no input.jsonl above the output; pass --input")
    prompt = json.loads(Path(a.prompt or newest_prompt()).read_text())
    limits = (prompt["meta"]["limit_instruction"], prompt["meta"]["limit_description"])
    fake = SimpleNamespace(example_lines={ln.strip() for m in prompt["examples"] if m["role"] == "assistant"
                                          for ln in m["content"].splitlines() if len(ln.strip()) >= 20})
    lex = ste_check.Lexicon.load()

    inputs = [json.loads(line) for line in open(in_path, encoding="utf-8") if line.strip()]
    outputs = {}
    for n, line in enumerate(open(out_path, encoding="utf-8"), 1):
        if line.strip():
            try:
                r = json.loads(line)
                outputs[r["id"]] = r["output"]
            except (json.JSONDecodeError, KeyError) as e:
                print(f"output line {n} unreadable: {e}", file=sys.stderr)

    by = defaultdict(lambda: [0, 0])
    rules, verbs, rows = Counter(), Counter(), []
    for rec in inputs:
        out = outputs.get(rec["id"])
        if out is None:
            res = {"ok": False, "hard": {"missing": [True]}, "soft": {}}
        else:
            res = ste_check.check(out, rec["text"], lex, *limits)
            ste_convert.Converter.guard(fake, res, out, rec["text"])
        by[rec["kind"]][0] += 1
        by[rec["kind"]][1] += res["ok"]
        rules.update(res["hard"].keys())
        verbs.update(res["hard"].get("verbs", []))
        rows.append((rec, out, res))

    total = sum(v[0] for v in by.values())
    print(f"{out_path}: {sum(v[1] for v in by.values())}/{total} pass, {len(outputs)} outputs")
    for k, (n, ok) in by.items():
        print(f"  {k}: {ok}/{n}")
    print("  failing rules:", dict(rules.most_common()))
    print("  failing verbs:", verbs.most_common(15))

    md = Path(a.md) if a.md else out_path.with_name("score.md")
    lines = [f"# STE100 rewrite score: {out_path.parent.name}", "",
             f"{sum(v[1] for v in by.values())}/{total} pass. "
             + ", ".join(f"{k} {ok}/{n}" for k, (n, ok) in by.items()), ""]
    for rec, out, res in rows:
        verdict = "pass" if res["ok"] else "FAIL " + ", ".join(
            f"{k} {v}" if k in ("verbs", "ing", "word_ratio") else k for k, v in res["hard"].items())
        lines += [f"## {rec['id']} {rec['kind']}: {verdict}", "", "Original:", "", "```text",
                  rec["text"].strip(), "```", "", "Rewrite:", "", "```text", (out or "").strip(), "```", ""]
    md.write_text("\n".join(lines), encoding="utf-8")
    print(f"  side by side: {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
