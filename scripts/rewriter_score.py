"""Spec 0.1 REWRITE-KIT, REWRITE-DECODE and REWRITE-CHECK foundations.

These helpers verify local inputs, preflight prompts and check first outputs.
They expose no model-run command while the seed-record interface is pending.
Checker artifacts are not complete R0 or adapter evaluation records.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "src")]

import rewriter_build as build
import ste_check
import ste_convert
import ste_experiment
import ste_score
from ste_kit_sample import bin_of
from weaver_train import manifest as mf


class ScoreError(ValueError):
    """REWRITE-LIMITS: stop on an invalid score input without a fallback."""


def require(condition, message):
    """REWRITE-KIT, REWRITE-CHECK: report the clause that refuses an input."""
    if not condition:
        raise ScoreError(message)


def jsonl_bytes(rows):
    """REWRITE-KIT, REWRITE-RECORD: give local artifact rows a stable byte identity."""
    return "".join(json.dumps(row, ensure_ascii=True, separators=(",", ":")) + "\n"
                   for row in rows).encode("utf-8")


def load_kit(repo=REPO):
    """REWRITE-KIT: verify both source kits and mask the fixed 100 source texts."""
    evidence = build.Evidence(repo)
    pins = tomllib.loads(evidence.read("config/rewriter-inputs.toml").decode())
    sources = {}
    for size in (100, 500):
        entries = evidence.rows(f"var/ste/kit-{size}/source.jsonl",
                                pins[f"kit_{size}_source"])
        sources[size] = list(build.unique(entries).values())
        require(len(entries) == size, f"REWRITE-KIT: expected {size} source records")
    rows = []
    for entry in sources[100]:
        source = entry.data
        text, codes = ste_convert.mask(source["raw"])
        rows.append({**source, "text": text, "codes": codes, "bin": bin_of(len(text))})
    require(len({r["uuid"] for r in rows}) == 100,
            "REWRITE-KIT: duplicate trajectory UUID")
    require(Counter(r["kind"] for r in rows) == {"narration": 90, "final": 10},
            "REWRITE-KIT: source kinds differ from 90/10")
    require(Counter(r["bin"] for r in rows) == {0: 37, 1: 30, 2: 33},
            "REWRITE-KIT: masked length bins differ from 37/30/33")
    evidence.verify()
    inputs = [{key: r[key] for key in ("id", "kind", "text")} for r in rows]
    return {"rows": rows, "inputs": evidence.files, "source_seed": 2030,
            "input_sha256": build.digest(jsonl_bytes(inputs))}


def reply_budget(masked_input):
    """REWRITE-DECODE: use the reference first-reply budget without a repair budget."""
    return min(8192, len(masked_input) // 2 + 256)


def preflight(tokenizer, kit, folded: bytes, *, repo=REPO):
    """REWRITE-DECODE, RENDER-LAYOUT: check all 100 prompts before any generation.

    This returns an in-memory plan only. A matching file hash does not establish
    seed provenance. The future run entry point must validate the seed record.
    """
    pins = tomllib.loads((repo / "config/rewriter-inputs.toml").read_text(encoding="utf-8"))
    require(build.digest(folded) == pins["folded_prompt"],
            "RENDER-SEED: folded prompt sha256 differs")
    system = folded.decode("utf-8")
    require(len(kit["rows"]) == 100 and len({r["id"] for r in kit["rows"]}) == 100,
            "REWRITE-KIT: expected 100 distinct inputs")
    plan = []
    for row in kit["rows"]:
        messages = build.initial_messages(system, row["text"])
        ids = tokenizer.apply_chat_template(
            messages, tokenize=True, return_dict=False,
            add_generation_prompt=True, enable_thinking=False)
        budget = reply_budget(row["text"])
        require(len(ids) + budget <= build.MAX_SEQ_LENGTH,
                f"REWRITE-DECODE: {row['id']} prompt plus reply exceeds 49152")
        message_bytes = json.dumps(messages, ensure_ascii=True,
                                   separators=(",", ":")).encode("utf-8")
        plan.append({"id": row["id"], "input_ids": ids, "max_new_tokens": budget,
                     "initial_messages_sha256": build.digest(message_bytes)})
    return plan


def prepare_outputs(kit, outputs):
    """REWRITE-CHECK: reject repeated or unknown IDs before the reference scorer."""
    ids = [row["id"] for row in kit["rows"]]
    require(len(ids) == 100 and len(set(ids)) == 100,
            "REWRITE-KIT: expected 100 distinct inputs")
    indexed = {}
    for raw in outputs:
        key = raw["id"]
        require(key in ids and key not in indexed,
                f"REWRITE-CHECK: duplicate or unknown output ID: {key}")
        require(raw["output"] is None or isinstance(raw["output"], str),
                f"REWRITE-CHECK: output is not text: {key}")
        require(raw["finish_reason"] in ("stop", "length"),
                f"REWRITE-CHECK: unknown finish reason: {key}")
        require(isinstance(raw["token_ids"], list) and all(
            type(token) is int and token >= 0 for token in raw["token_ids"]),
            f"REWRITE-RECORD: invalid raw tokens: {key}")
        seconds = raw["wall_seconds"]
        require(type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0,
                f"REWRITE-RECORD: invalid wall time: {key}")
        indexed[key] = raw
    retained, scorer_rows = [], []
    for row in kit["rows"]:
        raw = indexed.get(row["id"])
        text = raw["output"].strip() if raw and raw["output"] is not None else None
        included = bool(text) and raw["finish_reason"] == "stop"
        retained.append({"id": row["id"], "raw": raw, "output": text,
                         "scorer_included": included})
        if included:
            scorer_rows.append({"id": row["id"], "output": text})
    return retained, scorer_rows


def checker_inputs(repo):
    """REWRITE-CHECK: pin the dictionary and original checker bundle before use."""
    evidence = build.Evidence(repo)
    pins = tomllib.loads(evidence.read("config/rewriter-inputs.toml").decode())
    evidence.read("var/ste/approved-verbs.json", pins["approved_verbs"])
    prompt_bytes = evidence.read(f"var/ste/{build.PROMPT}")
    prompt = json.loads(prompt_bytes)
    bundle = build.digest(json.dumps([prompt["system"], prompt["examples"]]).encode())
    require(bundle == pins["single_text_bundle"] == prompt["meta"]["prompt_sha256"],
            "REWRITE-CHECK: source bundle sha256 differs")
    require(build.digest(prompt["system"].encode()) == pins["system_text"],
            "REWRITE-CHECK: source system sha256 differs")
    require(prompt["meta"]["verbs_sha256"] == pins["approved_verbs"] and
            (prompt["meta"]["limit_instruction"], prompt["meta"]["limit_description"]) == (20, 25),
            "REWRITE-CHECK: checker dictionary or sentence limits differ")
    return prompt, prompt_bytes, evidence


def check_outputs(kit, outputs, out_dir, *, repo=REPO):
    """REWRITE-CHECK, REWRITE-RECORD: retain raw outputs and a fresh checker artifact.

    The frozen scorer determines STE acceptance. Literal failures are recorded
    separately for the code-restoration guard in REWRITE-REVIEW.
    """
    retained, scorer_rows = prepare_outputs(kit, outputs)
    prompt, prompt_bytes, evidence = checker_inputs(repo)
    out = Path(out_dir).resolve()
    manifest_path = out.with_name(out.name + ".manifest.json")
    for path in (out, manifest_path):
        if path.is_relative_to(repo.resolve()):
            ignored = subprocess.run(["git", "check-ignore", "--quiet", str(path)], cwd=repo)
            require(ignored.returncode == 0, "REWRITE-RECORD: outputs must stay outside git")
    require(not out.exists() and not manifest_path.exists(),
            "REWRITE-RECORD: output directory or manifest already exists")
    checks = []
    out.mkdir(parents=True, exist_ok=False)
    try:
        inputs = [{key: row[key] for key in ("id", "kind", "text")} for row in kit["rows"]]
        input_bytes = jsonl_bytes(inputs)
        require(build.digest(input_bytes) == kit["input_sha256"],
                "REWRITE-KIT: masked inputs changed before scoring")
        (out / "input.jsonl").write_bytes(input_bytes)
        (out / "output.jsonl").write_bytes(jsonl_bytes(scorer_rows))
        (out / "raw.jsonl").write_bytes(jsonl_bytes(retained))
        (out / build.PROMPT).write_bytes(prompt_bytes)
        evidence.verify()
        lex = ste_check.Lexicon.load()
        evidence.verify()
        fake = SimpleNamespace(example_lines={
            line.strip() for message in prompt["examples"] if message["role"] == "assistant"
            for line in message["content"].splitlines() if len(line.strip()) >= 20})
        with (out / "checks.jsonl").open("xb") as stream:
            for source, result in zip(kit["rows"], retained):
                if result["scorer_included"]:
                    checked = ste_check.check(result["output"], source["text"], lex, 20, 25)
                    ste_convert.Converter.guard(fake, checked, result["output"], source["text"])
                else:
                    checked = {"ok": False, "hard": {"missing": [True]}, "soft": {}}
                literals = ste_experiment.altered_literals(result["output"], source["text"])
                row = {**result, "kind": source["kind"], "bin": source["bin"],
                       "check": checked, "literal_failures": literals,
                       "output_characters": len(result["output"] or ""),
                       "output_tokens": len(result["raw"]["token_ids"]) if result["raw"] else None}
                stream.write(jsonl_bytes([row]))
                stream.flush()
                checks.append(row)
        status = ste_score.main([str(out / "output.jsonl"), "--input", str(out / "input.jsonl"),
                                 "--prompt", str(out / build.PROMPT), "--md", str(out / "score.md")])
        require(status == 0, "REWRITE-CHECK: reference scorer failed")
        evidence.verify()
        summary = {"schema": "weaver-train/rewriter-check/v1", "denominator": 100,
                   "passed": sum(row["check"]["ok"] for row in checks),
                   "scorer_outputs": len(scorer_rows), "checker_inputs": evidence.files,
                   "kit_inputs": kit["inputs"], "input_sha256": kit["input_sha256"],
                   "literal_failure_segments": sum(bool(r["literal_failures"]) for r in checks)}
        summary["rate"] = summary["passed"] / 100
        # REWRITE-SPREAD: breakdowns keep missing and truncated outputs in each total.
        for field, values in (("kind", ("narration", "final")), ("bin", (0, 1, 2))):
            summary["by_" + field] = {str(value): {
                "total": sum(row[field] == value for row in checks),
                "passed": sum(row[field] == value and row["check"]["ok"] for row in checks),
            } for value in values}
        summary["failure_rules"] = dict(Counter(
            rule for row in checks for rule in row["check"]["hard"]))
        (out / "checks-summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        manifest = mf.build_tree(out, "evaluation", "ste-rewriter-check")
        with manifest_path.open("x", encoding="utf-8") as stream:
            stream.write(manifest.to_json())
    except Exception as exc:
        # REWRITE-RECORD: retain the failed attempt and refuse reuse of its directory.
        (out / "failure.json").write_text(json.dumps({
            "status": "failed", "error": f"{type(exc).__name__}: {exc}",
            "completed_ids": [row["id"] for row in checks],
        }, ensure_ascii=True) + "\n", encoding="utf-8")
        raise
    return summary, checks
