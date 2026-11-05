"""Spec 0.2 REWRITE-KIT, REWRITE-DECODE and REWRITE-CHECK foundations.

These helpers verify local inputs, preflight prompts and check first outputs.
The decode helper requires an already verified model and optional adapter.
The plan and run commands retain frozen inputs and complete first-pass records.
RENDER-SEED permits local procedures before the loop seed check.
REWRITE-RECONCILE still gates loop use.
Statistical helpers take checker rows after the caller verifies run identities.
"""

from __future__ import annotations

import json
import math
import random
import re
import subprocess
import sys
import time
import tomllib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "src")]

import rewriter_build as build
import ste_check
import ste_convert
import ste_experiment
import ste_score
from lora_score import compare_pairs
from ste_kit_sample import bin_of
from weaver_train import loader, manifest as mf

# REWRITE-RECORD, RENDER-OUTPUT: preserve the five fields and the review identity.
FIELDS = ("tree_hash", "template_hash", "tools", "enable_thinking", "max_seq_length")
REVIEW_MODEL = "claude-opus-5-5"


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


def input_count(kit, expected_count=100):
    """REWRITE-KIT, REWRITE-R1-E4-TRAIN-SCORES: keep each denominator explicit."""
    require(type(expected_count) is int and expected_count in (50, 100),
            "REWRITE-KIT: unsupported input count")
    clause = "REWRITE-KIT" if expected_count == 100 else "REWRITE-R1-E4-TRAIN-SCORES"
    require(len(kit["rows"]) == expected_count and
            len({row["id"] for row in kit["rows"]}) == expected_count,
            f"{clause}: expected {expected_count} distinct inputs")
    return expected_count


def preflight(tokenizer, kit, folded: bytes, *, repo=REPO, expected_count=100):
    """REWRITE-DECODE, RENDER-LAYOUT: check all 100 prompts before any generation.

    This returns an in-memory plan only. A matching file hash does not establish
    seed provenance. REWRITE-RECONCILE checks the seed separately before loop use.
    """
    pins = tomllib.loads((repo / "config/rewriter-inputs.toml").read_text(encoding="utf-8"))
    require(build.digest(folded) == pins["folded_prompt"],
            "RENDER-SEED: folded prompt sha256 differs")
    system = folded.decode("utf-8")
    input_count(kit, expected_count)
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


def decode_outputs(loaded, kit, folded: bytes, *, repo=REPO, expected_count=100):
    """REWRITE-DECODE, REWRITE-LIMITS: yield each first rewrite without a retry.

    The caller must use the current loader to verify and load the model, verify
    any adapter before attachment, and establish folded-file and plan provenance.
    This generator does not write a run record or authorize production use.
    The caller must retain yielded rows and record any failure under REWRITE-RECORD.
    """
    import torch
    from transformers import GenerationConfig

    model, tokenizer = loaded.model, loaded.tokenizer
    plan = preflight(tokenizer, kit, folded, repo=repo, expected_count=expected_count)
    end = tokenizer.convert_tokens_to_ids("<|im_end|>")
    require(type(end) is int and end >= 0 and end != tokenizer.unk_token_id,
            "REWRITE-DECODE: end-of-turn token is unavailable")
    devices = {parameter.device for parameter in model.parameters()}
    require(devices and all(device.type == "cuda" for device in devices),
            "REWRITE-LIMITS: model has a parameter off GPU")
    loader.pin_layer_devices(model)
    model.eval()
    device = model.get_input_embeddings().weight.device
    for entry in plan:
        for gpu in devices:
            torch.cuda.synchronize(gpu)
        started = time.monotonic()
        ids = torch.tensor([entry["input_ids"]], dtype=torch.long, device=device)
        config = GenerationConfig(
            do_sample=False, num_beams=1, num_return_sequences=1,
            return_dict_in_generate=False,
            max_new_tokens=entry["max_new_tokens"], eos_token_id=end,
            pad_token_id=tokenizer.pad_token_id if tokenizer.pad_token_id is not None
            else end)
        with torch.inference_mode():
            output = model.generate(input_ids=ids, attention_mask=torch.ones_like(ids),
                                    generation_config=config)
        require(output.ndim == 2 and output.shape[0] == 1 and
                output.shape[1] >= ids.shape[1] and
                torch.equal(output[0, :ids.shape[1]], ids[0]),
                "REWRITE-DECODE: generated sequence changed the prompt")
        tokens = output[0, ids.shape[1]:].tolist()
        require(0 < len(tokens) <= entry["max_new_tokens"],
                "REWRITE-DECODE: generated token count is outside the reply budget")
        stopped = tokens[-1] == end
        require(end not in tokens[:-1] and
                (stopped or len(tokens) == entry["max_new_tokens"]),
                "REWRITE-DECODE: output has no valid stop or length finish")
        text = tokenizer.decode(tokens[:-1] if stopped else tokens,
                                skip_special_tokens=False,
                                clean_up_tokenization_spaces=False)
        for gpu in devices:
            torch.cuda.synchronize(gpu)
        yield {"id": entry["id"], "output": text, "token_ids": tokens,
               "finish_reason": "stop" if stopped else "length",
               "wall_seconds": time.monotonic() - started}


def prepare_outputs(kit, outputs, *, expected_count=100):
    """REWRITE-CHECK: reject repeated or unknown IDs before the reference scorer."""
    ids = [row["id"] for row in kit["rows"]]
    input_count(kit, expected_count)
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
    require(repo.resolve() == ste_check.REPO.resolve() == ste_score.REPO.resolve(),
            "REWRITE-CHECK: repository differs from the frozen scorer root")
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


def check_outputs(kit, outputs, out_dir, *, repo=REPO, expected_count=100):
    """REWRITE-CHECK, REWRITE-RECORD: retain raw outputs and a fresh checker artifact.

    The frozen scorer determines STE acceptance. Literal failures are recorded
    separately for the code-restoration guard in REWRITE-REVIEW.
    """
    retained, scorer_rows = prepare_outputs(kit, outputs, expected_count=expected_count)
    prompt, prompt_bytes, evidence = checker_inputs(repo)
    out = Path(out_dir).resolve()
    manifest_path = out.with_name(out.name + ".manifest.json")
    for path in (out, manifest_path):
        if path.is_relative_to(repo.resolve()):
            name = str(path) + "/" if path == out else str(path)
            ignored = subprocess.run(["git", "check-ignore", "--quiet", name], cwd=repo)
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
        require(all(row.get("bin") == bin_of(len(row["text"])) for row in kit["rows"]),
                "REWRITE-KIT: source length bins changed before scoring")
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
        # REWRITE-CHECK: the frozen scorer supplies the authoritative rate.
        lines = (out / "score.md").read_text(encoding="utf-8").splitlines()
        headline = re.fullmatch(r"(\d+)/(\d+) pass\..*", lines[2]) if len(lines) > 2 else None
        require(headline is not None, "REWRITE-CHECK: reference headline is missing")
        passed, total = map(int, headline.groups())
        require(total == expected_count and passed == sum(row["check"]["ok"] for row in checks),
                "REWRITE-CHECK: per-ID results differ from the reference scorer")
        summary = {"schema": "weaver-train/rewriter-check/v1", "denominator": expected_count,
                   "passed": passed,
                   "empty_outputs": sum(row["output"] == "" for row in retained),
                   "scorer_outputs": len(scorer_rows), "checker_inputs": evidence.files,
                   "kit_inputs": kit["inputs"], "input_sha256": kit["input_sha256"],
                   "literal_failure_segments": sum(bool(r["literal_failures"]) for r in checks)}
        summary["rate"] = summary["passed"] / expected_count
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


def rewrite_outcomes(source_ids, checks, *, expected_count=100):
    """REWRITE-KIT, REWRITE-SPREAD: align exactly 100 boolean results by source ID."""
    require(type(expected_count) is int and expected_count in (50, 100),
            "REWRITE-KIT: unsupported outcome count")
    require(len(source_ids) == expected_count and all(type(key) is str and key for key in source_ids),
            f"REWRITE-KIT: comparisons require {expected_count} source IDs")
    expected = set(source_ids)
    require(len(expected) == expected_count, "REWRITE-KIT: comparison source IDs repeat")
    indexed = {}
    for row in checks:
        key = row.get("id")
        require(type(key) is str and key in expected and key not in indexed,
                "REWRITE-SPREAD: duplicate or unknown checker ID")
        check = row.get("check")
        require(isinstance(check, dict) and type(check.get("ok")) is bool,
                f"REWRITE-SPREAD: boolean checker result required for {key}")
        indexed[key] = check["ok"]
    require(indexed.keys() == expected, "REWRITE-SPREAD: checker results are missing")
    return [indexed[key] for key in source_ids]


def _spread_outcomes(first, second):
    """REWRITE-SPREAD: summarize two already validated and aligned outcome lists."""
    return {"denominator": 100,
            "r0_first": {"passed": sum(first), "rate": sum(first) / 100},
            "r0_second": {"passed": sum(second), "rate": sum(second) / 100},
            "decode_difference": abs(sum(first) - sum(second)) / 100}


def rewrite_spread(source_ids, r0_first, r0_second):
    """REWRITE-SPREAD: record both R0 rates and their absolute decode difference.

    This is a calculation on checker rows. The run reader must establish two
    untrained runs with identical frozen inputs, settings and model identities.
    """
    first = rewrite_outcomes(source_ids, r0_first)
    second = rewrite_outcomes(source_ids, r0_second)
    return _spread_outcomes(first, second)


def compare_rewrites(source_ids, r0_first, r0_second, candidate):
    """REWRITE-SPREAD, Spec 0.3 RUN-MARGIN: always compare against the first R0.

    Use the unchanged sign test, Newcombe interval and decode-margin rules.
    This function verifies result membership, not model or run provenance.
    The run reader must verify those identities before using these rows.
    """
    first = rewrite_outcomes(source_ids, r0_first)
    second = rewrite_outcomes(source_ids, r0_second)
    current = rewrite_outcomes(source_ids, candidate)
    spread = _spread_outcomes(first, second)
    return {"metric": "rewrite_ste", "reference": "r0_first", **spread,
            "candidate": {"passed": sum(current), "rate": sum(current) / 100},
            "comparison": compare_pairs(first, current, spread["decode_difference"])}


def artifact(directory):
    """REWRITE-RECORD: verify every file before reading an artifact's contents."""
    directory = Path(directory).resolve()
    path = directory.with_name(directory.name + ".manifest.json")
    raw = path.read_bytes()
    manifest = mf.TreeManifest.from_dict(json.loads(raw))
    mf.require_tree(manifest, directory)
    return directory, manifest, build.digest(raw)


def captured_file(directory, manifest, name):
    """REWRITE-RECORD: bind the bytes consumed after verification to the manifest."""
    expected = {entry.path: entry.sha256 for entry in manifest.files}
    raw = (directory / name).read_bytes()
    require(build.digest(raw) == expected.get(name),
            f"REWRITE-RECORD: captured file differs from manifest: {name}")
    return raw


def score_evidence(repo):
    """REWRITE-KIT, REWRITE-CHECK, REWRITE-RECORD: freeze the checked local inputs."""
    evidence = build.Evidence(repo)
    pins = tomllib.loads(evidence.read("config/rewriter-inputs.toml").decode())
    provenance = build.prompt_inputs(evidence, pins)
    # REWRITE-REVIEW: bind the approved prompt bytes to their pinned digest.
    evidence.read("config/ste-rewrite-review-prompt.md", pins["meaning_review_prompt"])
    for path in ("scripts/ste_score.py", "scripts/ste_prompt.py",
                 "scripts/ste_kit_run.py", "environment.lock", "config/models.toml"):
        evidence.read(path)
    return evidence, pins, provenance


def require_fields(record, expected):
    """RENDER-OUTPUT, REWRITE-DECODE: field values and types must remain identical."""
    for key in FIELDS:
        require(key in record and type(record[key]) is type(expected[key]) and
                record[key] == expected[key], f"RENDER-OUTPUT: {key} differs")


def fresh_score_output(out_dir, *, repo, inputs=()):
    """REWRITE-RECORD: reserve fresh evidence outside git and verified input trees."""
    out, manifest_path = build.fresh_build_output(out_dir, repo)
    for path in inputs:
        root = Path(path).resolve()
        require(out != root and root not in out.parents and out not in root.parents,
                "REWRITE-RECORD: output overlaps an input tree")
    for path in (out, *out.parents):
        exists = path.name and path.with_name(path.name + ".manifest.json").is_file()
        require(not exists,
                "REWRITE-RECORD: output is inside a manifested tree")
    return out, manifest_path


def freeze_plan(build_dir, folded_path, out_dir, *, repo=REPO):
    """REWRITE-KIT, REWRITE-DECODE, REWRITE-REVIEW: freeze the plan before R0."""
    out, manifest_path = fresh_score_output(out_dir, repo=repo, inputs=(build_dir,))
    commit = mf.git_commit(repo)
    require(isinstance(commit["rev"], str) and bool(commit["rev"]) and
            commit["dirty"] is False,
            "REWRITE-RECORD: a known clean checkout is required to freeze the plan")
    evidence, pins, provenance = score_evidence(repo)
    directory, build_manifest, manifest_sha = artifact(build_dir)
    require(build_manifest.kind == "dataset" and
            build_manifest.name == "ste-rewriter-build",
            "REWRITE-RECORD: expected a rewriter build manifest")
    build_bytes = captured_file(directory, build_manifest, "build.json")
    record = json.loads(build_bytes)
    require(record["schema"] == "weaver-train/rewriter-build/v1" and
            record["status"] == "completed" and not record["render_only"],
            "REWRITE-RECORD: a completed rewriter training build is required")
    require(record.get("commit") == commit,
            "REWRITE-RECORD: build checkout differs from plan checkout")
    folded = Path(folded_path).read_bytes()
    build.folded_system(folded, pins["folded_prompt"])
    require(record["folded_prompt"] == pins["folded_prompt"] and
            record["prompt_provenance"] == provenance,
            "RENDER-SEED: build prompt identities differ")
    tokenizer, model_manifest, model_dir, model_hash = build.build_tokenizer(evidence)
    fresh_score_output(out, repo=repo, inputs=(build_dir, model_dir))
    expected = {"tree_hash": model_hash,
                "template_hash": build.digest(tokenizer.get_chat_template().encode()),
                "tools": False, "enable_thinking": False, "max_seq_length": 49152}
    require_fields(record, expected)
    kit = load_kit(repo)
    entries = preflight(tokenizer, kit, folded, repo=repo)
    end = tokenizer.convert_tokens_to_ids("<|im_end|>")
    require(type(end) is int and end >= 0 and end != tokenizer.unk_token_id,
            "REWRITE-DECODE: end-of-turn token is unavailable")
    sample = random.Random(2031).sample(sorted(row["id"] for row in kit["rows"]), 30)
    plan = {"schema": "weaver-train/rewriter-plan/v1", "commit": commit,
            **expected, "folded_prompt": pins["folded_prompt"],
            "prompt_provenance": provenance, "inputs": evidence.files,
            "environment_sha256": evidence.files["environment.lock"]["sha256"],
            "build_sha256": build.digest(build_bytes),
            "build_manifest_sha256": manifest_sha,
            "build_tree_hash": build_manifest.tree_hash,
            "input_sha256": kit["input_sha256"], "denominator": 100,
            "source_seed": 2030, "review_sample_seed": 2031, "review_sample": sample,
            "reviewer_model": REVIEW_MODEL,
            "reviewer_prompt_sha256": pins["meaning_review_prompt"],
            "sampler": {"do_sample": False, "num_beams": 1, "num_return_sequences": 1,
                        "eos_token_id": end, "pad_token_id": tokenizer.pad_token_id
                        if tokenizer.pad_token_id is not None else end,
                        "reply_budget": "min(8192, len(masked_input) // 2 + 256)"},
            "loop_checks": {"seed_check_complete": False,
                            "joint_token_check_complete": False}}
    if record["procedure"] == "r1-e4":
        plan["procedure"] = "r1-e4"
    evidence.verify()
    mf.require_tree(model_manifest, model_dir)
    require(mf.git_commit(repo) == commit,
            "REWRITE-RECORD: checkout changed while freezing the plan")
    inputs = [{key: row[key] for key in ("id", "kind", "text")} for row in kit["rows"]]
    plan_bytes = (json.dumps(plan, indent=2, ensure_ascii=True) + "\n").encode()
    files = {"plan.json": plan_bytes,
             "kit.json": (json.dumps(kit, ensure_ascii=True) + "\n").encode(),
             "entries.jsonl": jsonl_bytes(entries), "input.jsonl": jsonl_bytes(inputs),
             "folded.txt": folded}
    out.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items():
        with (out / name).open("xb") as stream:
            stream.write(raw)
    manifest = mf.build_tree(out, "evaluation", "ste-rewriter-plan")
    with manifest_path.open("x", encoding="utf-8") as stream:
        stream.write(manifest.to_json())
    return plan, manifest


def read_plan(plan_dir, *, repo=REPO):
    """REWRITE-KIT, REWRITE-RECORD: check frozen sources, settings and identities."""
    directory, manifest, manifest_sha = artifact(plan_dir)
    if manifest.name == "ste-rewriter-training-plan":
        from rewriter_r1_e4 import read_training_plan

        return read_training_plan(directory, manifest, manifest_sha, repo)
    require(manifest.kind == "evaluation" and manifest.name == "ste-rewriter-plan",
            "REWRITE-RECORD: expected a frozen rewriter plan")
    raw = {name: captured_file(directory, manifest, name) for name in
           ("plan.json", "kit.json", "entries.jsonl", "input.jsonl", "folded.txt")}
    plan, kit = json.loads(raw["plan.json"]), json.loads(raw["kit.json"])
    entries = [json.loads(line) for line in raw["entries.jsonl"].splitlines()]
    require(plan["schema"] == "weaver-train/rewriter-plan/v1" and
            plan["denominator"] == 100, "REWRITE-KIT: plan schema or count differs")
    evidence, pins, provenance = score_evidence(repo)
    require(plan["inputs"] == evidence.files,
            "REWRITE-RECORD: frozen input identities differ")
    require(plan["environment_sha256"] == evidence.files["environment.lock"]["sha256"],
            "REWRITE-RECORD: frozen environment differs")
    require(kit == load_kit(repo), "REWRITE-KIT: frozen source records differ")
    inputs = [{key: row[key] for key in ("id", "kind", "text")} for row in kit["rows"]]
    require(raw["input.jsonl"] == jsonl_bytes(inputs) and
            plan["input_sha256"] == kit["input_sha256"],
            "REWRITE-KIT: frozen masked inputs differ")
    build.folded_system(raw["folded.txt"], pins["folded_prompt"])
    require(plan["folded_prompt"] == pins["folded_prompt"] and
            plan["prompt_provenance"] == provenance,
            "RENDER-SEED: frozen prompt identities differ")
    sample = random.Random(2031).sample(sorted(row["id"] for row in kit["rows"]), 30)
    require(plan["source_seed"] == 2030 and plan["review_sample"] == sample and
            plan["review_sample_seed"] == 2031 and
            plan["reviewer_model"] == REVIEW_MODEL and
            plan["reviewer_prompt_sha256"] == pins["meaning_review_prompt"],
            "REWRITE-REVIEW: frozen review sample or prompt differs")
    require([entry["id"] for entry in entries] == [row["id"] for row in kit["rows"]],
            "REWRITE-KIT: frozen prompt membership differs")
    evidence.verify()
    return plan, kit, entries, raw["folded.txt"], manifest.tree_hash, manifest_sha


def validate_loaded_plan(plan, kit, entries, folded, loaded, *, repo, expected_count=100):
    """REWRITE-DECODE: compare the complete loaded render before the first output."""
    tok = loaded.tokenizer
    expected = {"tree_hash": loaded.tree_hash,
                "template_hash": build.digest(tok.get_chat_template().encode()),
                "tools": False, "enable_thinking": False, "max_seq_length": 49152}
    require_fields(plan, expected)
    require(entries == preflight(tok, kit, folded, repo=repo, expected_count=expected_count),
            "REWRITE-DECODE: frozen prompt tokens or reply budgets differ")
    end = tok.convert_tokens_to_ids("<|im_end|>")
    require(type(end) is int and end >= 0 and end != tok.unk_token_id,
            "REWRITE-DECODE: end-of-turn token is unavailable")
    sampler = {"do_sample": False, "num_beams": 1, "num_return_sequences": 1,
               "eos_token_id": end, "pad_token_id": tok.pad_token_id
               if tok.pad_token_id is not None else end,
               "reply_budget": "min(8192, len(masked_input) // 2 + 256)"}
    require(plan["sampler"] == sampler, "REWRITE-DECODE: frozen sampler differs")


def adapter_producer(adapter, manifest, training_build, procedure, epoch, plan, repo):
    """REWRITE-CONFIG, REWRITE-RECORD: bind an epoch adapter to its completed train."""
    raw = (adapter.parent / "run.json").read_bytes()
    producer = mf.RunRecord(**json.loads(raw))
    producer.validate()
    require(producer.schema == mf.RUN_SCHEMA and producer.kind == "train" and
            producer.status == "completed" and producer.precision == "bf16",
            "REWRITE-RECORD: a completed bf16 training record is required")
    require(producer.model == {"name": build.MODEL, "tree_hash": plan["tree_hash"]},
            "REWRITE-RECORD: adapter producer used a different base model")
    directory, data_manifest, data_sha = artifact(training_build)
    require(data_manifest.kind == "dataset" and
            data_manifest.name == "ste-rewriter-build",
            "REWRITE-RECORD: adapter needs a rewriter training build")
    build_bytes = captured_file(directory, data_manifest, "build.json")
    record = json.loads(build_bytes)
    require(record["schema"] == "weaver-train/rewriter-build/v1" and
            record["status"] == "completed" and record["render_only"] is False and
            record["procedure"] == procedure,
            "REWRITE-RECORD: adapter training procedure differs")
    require(record.get("commit") == plan["commit"],
            "REWRITE-RECORD: adapter training build checkout differs from frozen plan")
    require_fields(record, plan)
    require(record["folded_prompt"] == plan["folded_prompt"] and
            record["prompt_provenance"] == plan["prompt_provenance"],
            "RENDER-SEED: adapter training prompt differs")
    pins = tomllib.loads((repo / "config/rewriter-inputs.toml").read_text())
    selection_name = "r1" if procedure == "r1-e4" else procedure
    require(record["counts"]["pairs"] == build.PROCEDURES[selection_name] and
            record["selection"]["ordered_ids_sha256"] ==
            pins[f"{selection_name}_ordered_ids"],
            "PAIR-BALANCE: adapter training selection differs")
    train_sha = build.digest(captured_file(directory, data_manifest, "train.jsonl"))
    expected_data = {"sha256": train_sha, "build_sha256": build.digest(build_bytes),
                     "rows": record["counts"]["pairs"],
                     "assistant_tokens": record["counts"]["final_target_tokens"]}
    require(isinstance(producer.dataset, dict) and
            all(producer.dataset.get(key) == value
                for key, value in expected_data.items()),
            "REWRITE-RECORD: adapter producer used a different training build")
    extra = {}
    config_path = "config/rewriter-train.toml"
    if procedure == "r1-e4":
        import rewriter_r1_e4 as e4

        e4.configuration(repo)
        extra = e4.producer_proof(adapter.parent, producer, plan, repo)
        config_path = e4.CONFIG
    config_bytes = (repo / config_path).read_bytes()
    config = tomllib.loads(config_bytes.decode())
    require(producer.config == config and producer.seed == config["train"]["seed"],
            "REWRITE-CONFIG: adapter training values differ")
    epochs = [entry for entry in producer.results["epoch_adapters"]
              if type(entry["epoch"]) is int and entry["epoch"] == epoch]
    require(len(epochs) == 1 and epochs[0]["tree_hash"] == manifest.tree_hash,
            "REWRITE-CONFIG: requested epoch does not identify this adapter")
    return {"run_id": producer.run_id, "run_sha256": build.digest(raw),
            "build_tree_hash": data_manifest.tree_hash,
            "build_manifest_sha256": data_sha, **expected_data,
            "config_sha256": build.digest(config_bytes),
            "procedure": procedure, "epoch": epoch, **extra}


def run_score(plan_dir, out_dir, *, procedure, epoch=0, adapter=None,
              training_build=None, repo=REPO):
    """REWRITE-DECODE, REWRITE-CHECK, REWRITE-RECORD: retain one complete first pass."""
    import torch

    started = time.monotonic()
    require((procedure == "r0" and adapter is None and epoch == 0 and
             training_build is None) or
            (procedure in ("r1", "r2", "r1-e4") and adapter is not None and
             type(epoch) is int and epoch in ((1, 2, 3, 4) if procedure == "r1-e4" else (1, 2))
             and training_build is not None),
            "REWRITE-ORDER: adapter scores need an epoch and its training build")
    bundle = read_plan(plan_dir, repo=repo)
    plan, kit, entries, folded, plan_tree, plan_sha = bundle
    count = plan["denominator"]
    require(plan.get("procedure") != "r1-e4" or procedure == "r1-e4",
            "REWRITE-R1-E4: experiment plan requires the four-epoch procedure")
    require(count == 100 or procedure == "r1-e4",
            "REWRITE-R1-E4-TRAIN-SCORES: training sample requires R1-E4")
    experiment = None
    if procedure == "r1-e4":
        import rewriter_r1_e4 as e4
        import rewriter_provenance as provenance

        experiment = e4.score_plan(plan_dir, bundle, repo)
    commit = mf.git_commit(repo)
    model_manifest_path = loader.manifest_path_for(build.MODEL)
    model_manifest = mf.load_tree(model_manifest_path)
    configured = tomllib.loads((repo / "config/models.toml").read_text())["models"][
        build.MODEL]
    require(model_manifest.kind == "model" and model_manifest.name == build.MODEL and
            all(model_manifest.source.get(key) == configured[key]
                for key in ("repo_id", "revision")),
            "REWRITE-LIMITS: model manifest differs from configured reference")
    require(model_manifest.tree_hash == plan["tree_hash"],
            "REWRITE-DECODE: model manifest differs from frozen plan")
    model_dir = loader.resolve_dir(model_manifest)
    inputs = [plan_dir, model_dir]
    if adapter is not None:
        inputs.extend((adapter, training_build))
    out, manifest_path = fresh_score_output(out_dir, repo=repo, inputs=inputs)
    record = {"schema": "weaver-train/rewriter-run/v1", "run_id": uuid4().hex,
              "created_at": datetime.now(timezone.utc).isoformat(),
              "engine": "python-hf", "precision": "bf16", "status": "started",
              "commit": commit, "procedure": procedure, "epoch": epoch,
              "adapter": None, "model": build.MODEL,
              **{field: plan[field] for field in FIELDS},
              "environment_sha256": plan["environment_sha256"],
              "plan_tree_hash": plan_tree, "plan_manifest_sha256": plan_sha,
              "build_sha256": plan["build_sha256"],
              "input_sha256": plan["input_sha256"],
              "folded_prompt": plan["folded_prompt"],
              "prompt_provenance": plan["prompt_provenance"],
              "sampler": plan["sampler"],
              "device_map": "balanced", "completed_ids": [], "exclusions": [],
              "review": {"status": "pending", "model": plan["reviewer_model"],
                         "prompt_sha256": plan["reviewer_prompt_sha256"],
                         "sample": plan["review_sample"]},
              "loop_checks": {"seed_check_complete": False,
                              "joint_token_check_complete": False}}
    out.mkdir(parents=True, exist_ok=False)
    if experiment is not None:
        record["experiment"] = experiment

    def save_record():
        """REWRITE-RECORD: keep this invocation's status beside its outputs."""
        (out / "run.json").write_text(json.dumps(record, indent=2, ensure_ascii=True)
                                      + "\n", encoding="utf-8")

    save_record()
    devices = []
    try:
        require(commit == plan["commit"],
                "REWRITE-RECORD: checkout commit differs from frozen plan")
        if experiment is not None:
            record["imports_before"] = provenance.capture(repo)
        require(torch.cuda.is_available() and torch.cuda.device_count() == 2,
                "REWRITE-LIMITS: exactly two selected GPUs are required")
        # REWRITE-RECORD: the allocator must exist before peak counters are reset.
        torch.cuda.init()
        devices = list(range(torch.cuda.device_count()))
        for device in devices:
            torch.cuda.reset_peak_memory_stats(device)
        record["gpus"] = {str(device): {"name": torch.cuda.get_device_name(device),
            "capability": list(torch.cuda.get_device_capability(device))}
            for device in devices}
        adapter_manifest, adapter_path, adapter_sha = None, None, None
        if adapter is not None:
            adapter_path, adapter_manifest, adapter_sha = artifact(adapter)
            require(adapter_manifest.kind == "model" and
                    {entry.path for entry in adapter_manifest.files} >=
                    {"adapter_config.json", "adapter_model.safetensors"},
                    "REWRITE-DECODE: adapter files are missing")
            record["adapter"] = {"tree_hash": adapter_manifest.tree_hash,
                                 "manifest_sha256": adapter_sha,
                                 "producer": adapter_producer(
                                     adapter_path, adapter_manifest, training_build,
                                     procedure, epoch, plan, repo)}
            if experiment is not None:
                e4.verify_score_proof(record["adapter"]["producer"], bundle, experiment)
        loaded = loader.load(build.MODEL, path=model_dir,
                             manifest_path=model_manifest_path, device_map="balanced")
        validate_loaded_plan(plan, kit, entries, folded, loaded, repo=repo,
                             expected_count=count)
        if adapter_path is not None:
            from peft import PeftModel

            loaded.model = PeftModel.from_pretrained(
                loaded.model, str(adapter_path), is_trainable=False,
                local_files_only=True)
            loader.pin_layer_devices(loaded.model)
        placement = {parameter.device for parameter in loaded.model.parameters()}
        require(placement == {torch.device("cuda:0"), torch.device("cuda:1")},
                "REWRITE-LIMITS: all parameters must span the two selected GPUs")
        if experiment is not None:
            record["imports_before"] = provenance.capture(
                repo, previous=record["imports_before"])
        outputs = []
        with (out / "segments.jsonl").open("xb") as stream:
            for row in decode_outputs(loaded, kit, folded, repo=repo, expected_count=count):
                stream.write(jsonl_bytes([row]))
                stream.flush()
                outputs.append(row)
                record["completed_ids"].append(row["id"])
                print(f"rewritten {len(outputs)}/{count}: {row['id']}", flush=True)
        require(record["completed_ids"] == [entry["id"] for entry in entries],
                "REWRITE-DECODE: first outputs do not cover the frozen plan")
        summary, checks = check_outputs(kit, outputs, out / "checks", repo=repo,
                                       expected_count=count)
        record["checks"] = summary
        record["output_lengths"] = [{"id": row["id"],
            "characters": row["output_characters"], "tokens": row["output_tokens"]}
            for row in checks]
        # REWRITE-RECORD: no later edit may silently change the frozen run identities.
        final_plan = read_plan(plan_dir, repo=repo)
        require(final_plan[4:] == (plan_tree, plan_sha),
                "REWRITE-RECORD: plan changed during generation")
        mf.require_tree(model_manifest, model_dir)
        if adapter_path is not None:
            _, after, after_sha = artifact(adapter_path)
            require(after.tree_hash == adapter_manifest.tree_hash and
                    after_sha == adapter_sha,
                    "REWRITE-DECODE: adapter changed during generation")
            require(adapter_producer(adapter_path, after, training_build,
                                     procedure, epoch, plan, repo) ==
                    record["adapter"]["producer"],
                    "REWRITE-RECORD: adapter producer changed during generation")
        require(mf.git_commit(repo) == plan["commit"],
                "REWRITE-RECORD: checkout changed during generation")
        if experiment is not None:
            require(e4.score_plan(plan_dir, final_plan, repo) == experiment,
                    "REWRITE-R1-E4: experiment changed during generation")
            record["imports_after"] = provenance.capture(
                repo, previous=record["imports_before"])
        record["segments_sha256"] = mf.sha256_file(out / "segments.jsonl")
        record["status"] = "completed"
    except BaseException as exc:
        record["status"] = "failed"
        record["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        record["wall_seconds"] = time.monotonic() - started
        record["peak_memory_bytes"] = {str(device): {
            "allocated": torch.cuda.max_memory_allocated(device),
            "reserved": torch.cuda.max_memory_reserved(device)} for device in devices}
        save_record()
    try:
        manifest = mf.build_tree(out, "evaluation", "ste-rewriter-run")
        with manifest_path.open("x", encoding="utf-8") as stream:
            stream.write(manifest.to_json())
    except BaseException as exc:
        # REWRITE-RECORD: a failed seal keeps a failed record and all outputs.
        record["status"] = "failed"
        record["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        save_record()
        raise
    return record, manifest


def main(argv=None, *, repo=REPO):
    """REWRITE-KIT, REWRITE-DECODE: freeze a local plan or execute one first pass."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="freeze the inputs and render before R0")
    plan.add_argument("--build", type=Path, required=True)
    plan.add_argument("--folded-prompt", type=Path, required=True)
    plan.add_argument("--out", type=Path, required=True)
    run = commands.add_parser("run", help="record one first rewrite per frozen input")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--procedure", choices=("r0", "r1", "r2", "r1-e4"), required=True)
    run.add_argument("--epoch", type=int, default=0)
    run.add_argument("--adapter", type=Path)
    run.add_argument("--training-build", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            record, manifest = freeze_plan(args.build, args.folded_prompt, args.out,
                                            repo=repo)
            result = {"denominator": record["denominator"],
                      "review_sample": record["review_sample"]}
        else:
            record, manifest = run_score(args.plan, args.out, procedure=args.procedure,
                                         epoch=args.epoch, adapter=args.adapter,
                                         training_build=args.training_build, repo=repo)
            result = {"status": record["status"], "passed": record["checks"]["passed"]}
    except (mf.ManifestError, loader.LoadError, OSError, KeyError, TypeError,
            IndexError, ValueError, RuntimeError) as exc:
        print(f"{args.command}: stopped: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({**result, "tree_hash": manifest.tree_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
