"""Spec 0.3 SCORE clauses and RUN-MARGIN for held-out STE evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sys
import time
from pathlib import Path
from statistics import NormalDist

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "src")]
import lora_build as build
import ste_check
import ste_convert
import ste_experiment
from weaver_train import loader, manifest as mf

REVIEW_MODEL = "claude-opus-5-5"
CHECKER_PROMPT = "prompt-e21370fcd140.json"
CHECKER_SHA256 = "e21370fcd140a4bb5e7a0d2dd415c58d8ecead9333a9e730adb66ab96e7a851c"
FIELDS = ("tree_hash", "template_hash", "tools", "enable_thinking", "max_seq_length")
OUTPUT_LIMIT = 2048


class ScoreError(ValueError):
    """A score input violates the shared build record or frozen evaluation plan."""


def validate_build(record, tokenizer, tree_hash, max_seq_length, enable_thinking=False):
    """BUILD-RENDER, RECONCILE-FIELDS: scoring must use the build's exact settings."""
    expected = {"tree_hash": tree_hash, "template_hash": build.template_hash(tokenizer),
                "tools": True, "enable_thinking": enable_thinking, "max_seq_length": max_seq_length}
    for field in FIELDS:
        if field not in record or type(record[field]) is not type(expected[field]) or record[field] != expected[field]:
            raise ScoreError(f"RECONCILE-FIELDS: {field} is absent or differs from the scorer")
    if enable_thinking is not False:
        raise ScoreError("BUILD-RENDER: Spec 0.3 requires enable_thinking=false")


def locate_segments(kit, rows):
    """SCORE-LOCATE: require one uuid, location and original-text match in the dataset."""
    by_uuid = {}
    matches = {segment["id"]: [] for segment in kit}
    if len(matches) != len(kit):
        raise ScoreError("SCORE-HELDOUT: segment IDs must be unique")
    for segment in kit:
        by_uuid.setdefault(segment["uuid"], []).append(segment)
    for number, row in enumerate(rows):
        if row["uuid"] not in by_uuid:
            continue
        segments = list(ste_convert.segments(row))
        for wanted in by_uuid[row["uuid"]]:
            # SCORE-LOCATE: ste_kit_sample stores split_ws(text)[1], preserving all interior text.
            if any(loc == wanted["loc"] and ste_convert.split_ws(raw)[1] == ste_convert.split_ws(wanted["raw"])[1]
                   for loc, raw in segments):
                matches[wanted["id"]].append((number, row))
    located, excluded = {}, []
    for segment in kit:
        candidates = matches[segment["id"]]
        if len(candidates) == 1:
            located[segment["id"]] = candidates[0]
        else:
            excluded.append({"id": segment["id"], "cause": "SCORE-LOCATE", "matches": len(candidates)})
    return located, excluded


def make_plan(tokenizer, kit, rows, record, *, seed=0):
    """SCORE-HELDOUT, SCORE-PREFIX, SCORE-VALIDITY, SCORE-REVIEW: freeze eligibility before R0."""
    located, excluded = locate_segments(kit, rows)
    entries = []
    for segment in kit:
        if segment["id"] not in located:
            continue
        number, row = located[segment["id"]]
        index = segment["loc"]["msg"]
        prompt = {"messages": row["messages"][:index], "tools": row.get("tools")}
        ids = build.render_row(tokenizer, prompt, enable_thinking=record["enable_thinking"],
                               add_generation_prompt=True)
        if len(ids) + OUTPUT_LIMIT > record["max_seq_length"]:
            excluded.append({"id": segment["id"], "cause": "SCORE-PREFIX", "prompt_tokens": len(ids)})
            continue
        entries.append({"id": segment["id"], "kind": segment["kind"], "row": number,
                        "uuid": row["uuid"], "loc": segment["loc"], "prompt": prompt, "input_ids": ids,
                        "request": bool(row["messages"][index].get("tool_calls"))})
    eligible = [entry["id"] for entry in entries]
    review = random.Random(seed).sample(eligible, min(30, len(eligible)))
    return {**{field: record[field] for field in FIELDS}, "seed": seed, "entries": entries,
            "excluded": excluded, "request_set": [entry["id"] for entry in entries if entry["request"]],
            "review_sample": review, "review_count": len(review), "engine": "python-hf"}


def json_arguments(text):
    """SCORE-VALIDITY: reject non-JSON numeric constants and non-object arguments."""
    def reject(value):
        raise ValueError(f"invalid JSON constant {value}")
    value = json.loads(text, parse_constant=reject)
    if not isinstance(value, dict):
        raise ValueError("tool arguments must be a JSON object")
    return value


def parse_requests(output, tools):
    """SCORE-EXTRACT, SCORE-VALIDITY: parse the Nemotron template's tool request format."""
    schemas = {item.get("function", item)["name"]: item.get("function", item) for item in tools or []}
    blocks = list(re.finditer(r"<tool_call>\s*(.*?)\s*</tool_call>", output, re.S))
    requests = []
    for block in blocks:
        body = block.group(1)
        try:
            if body.startswith("{"):
                request = json_arguments(body)
                function = request.get("function", request)
                name = function["name"]
                arguments = function["arguments"]
                if isinstance(arguments, str):
                    arguments = json_arguments(arguments)
                else:
                    arguments = json_arguments(json.dumps(arguments, allow_nan=False))
            else:
                function = re.fullmatch(r"<function=([^<>\s]+)>\s*(.*?)\s*</function>", body, re.S)
                if function is None:
                    raise ValueError("malformed function block")
                name, parameters = function.groups()
                arguments = {}
                properties = schemas.get(name, {}).get("parameters", {}).get("properties", {})
                cursor = 0
                for parameter in re.finditer(r"<parameter=([^<>\s]+)>\s*\n?(.*?)\n?\s*</parameter>", parameters, re.S):
                    if parameters[cursor:parameter.start()].strip():
                        raise ValueError("unparsed parameter content")
                    key, value = parameter.groups()
                    if key in arguments:
                        raise ValueError("duplicate parameter")
                    value = value.strip()
                    if properties.get(key, {}).get("type") == "string":
                        arguments[key] = value
                    elif properties.get(key, {}).get("type") in ("object", "array", "number", "integer", "boolean", "null"):
                        # Typed non-string parameters must retain valid JSON syntax.
                        arguments[key] = json.loads(value)
                    else:
                        # A schema without a type can contain an unquoted string.
                        try:
                            arguments[key] = json.loads(value)
                        except json.JSONDecodeError:
                            arguments[key] = value
                    cursor = parameter.end()
                if parameters[cursor:].strip():
                    raise ValueError("unparsed parameter content")
                arguments = json_arguments(json.dumps(arguments, allow_nan=False))
            requests.append({"name": name, "arguments": arguments})
        except (ValueError, KeyError, TypeError) as exc:
            requests.append({"error": str(exc)})
    if output.count("<tool_call>") != len(blocks) or output.count("</tool_call>") != len(blocks):
        requests.append({"error": "incomplete tool request"})
    return requests


def extract_text(output, kind, requests):
    """SCORE-EXTRACT: narration precedes the first request, final text is finish.message."""
    if kind == "final":
        for request in requests:
            if request.get("name") == "finish":
                value = request.get("arguments", {}).get("message")
                if isinstance(value, str):
                    return value.strip()
        return ""
    return output.split("<tool_call>", 1)[0].strip()


def valid_requests(requests, tools):
    """SCORE-VALIDITY: every request needs JSON arguments, a known name and required keys."""
    schemas = {item.get("function", item)["name"]: item.get("function", item) for item in tools or []}
    if not requests:
        return False
    for request in requests:
        name, arguments = request.get("name"), request.get("arguments")
        if (request.get("error") or not isinstance(name, str) or name not in schemas
                or not isinstance(arguments, dict)):
            return False
        if not set(schemas[name].get("parameters", {}).get("required", [])) <= arguments.keys():
            return False
    return True


def evaluate_output(entry, output, judge):
    """SCORE-CHECK, SCORE-OUTPUT: score generated text, never compare it as a rewrite."""
    requests = parse_requests(output, entry["prompt"].get("tools"))
    text = extract_text(output, entry["kind"], requests)
    masked, _ = ste_convert.mask(text)
    result = judge(masked, masked) if text else {"ok": False, "hard": {"no_text": True}, "soft": {}}
    words = ste_check.WORD.findall(ste_check.PLACEHOLDER.sub(" ", masked))
    return {"id": entry["id"], "prompt_tokens": len(entry["input_ids"]), "output": output,
            "extracted_text": text, "check": result, "word_count": ste_check.count_words(masked),
            "ing_words": sum(word.lower().endswith("ing") for word in words),
            "requests": requests,
            "correct_request": valid_requests(requests, entry["prompt"].get("tools")) if entry["request"] else None}


def load_checker():
    """SCORE-CHECK: verify the original conversion prompt before constructing its Judge."""
    prompt = json.loads((REPO / "var/ste" / CHECKER_PROMPT).read_text())
    digest = hashlib.sha256(json.dumps([prompt["system"], prompt["examples"]]).encode()).hexdigest()
    if digest != CHECKER_SHA256 or prompt["meta"]["prompt_sha256"] != digest:
        raise ScoreError("SCORE-CHECK: conversion prompt hash differs from e21370fcd140")
    if (prompt["meta"]["limit_instruction"], prompt["meta"]["limit_description"]) != (20, 25):
        raise ScoreError("SCORE-CHECK: conversion prompt sentence limits differ")
    return ste_experiment.Judge(CHECKER_PROMPT), digest


def wilson(successes, total):
    """RUN-MARGIN: marginal Wilson score limits for Newcombe's paired method 10."""
    p = successes / total
    z = NormalDist().inv_cdf(0.975)
    center = (p + z * z / (2 * total)) / (1 + z * z / total)
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return center - radius, center + radius


def paired_interval(a, b):
    """RUN-MARGIN: Newcombe 1998 method 10, with the positive covariance correction.

    Formula reference: statsmodels.stats.proportion.confint_proportions_paired.
    https://www.statsmodels.org/dev/generated/statsmodels.stats.proportion.confint_proportions_paired.html
    """
    n = len(a)
    both = sum(x and y for x, y in zip(a, b))
    gain = sum(not x and y for x, y in zip(a, b))
    loss = sum(x and not y for x, y in zip(a, b))
    neither = n - both - gain - loss
    p, q = sum(b) / n, sum(a) / n
    lp, up = wilson(sum(b), n)
    lq, uq = wilson(sum(a), n)
    product = (both + gain) * (loss + neither) * (both + loss) * (gain + neither)
    covariance = both * neither - gain * loss
    if covariance > 0:
        covariance = max(0, covariance - n / 2)
    correlation = covariance / math.sqrt(product) if product else 0
    lower = math.hypot(p - lp, uq - q) ** 2 - 2 * correlation * (p - lp) * (uq - q)
    upper = math.hypot(up - p, q - lq) ** 2 - 2 * correlation * (up - p) * (q - lq)
    return p - q - math.sqrt(max(0, lower)), p - q + math.sqrt(max(0, upper))


def compare_pairs(a, b, decode_difference):
    """RUN-MARGIN: exact sign test, paired interval and the fixed decode margin."""
    if len(a) != len(b) or not a or any(type(v) is not bool for v in [*a, *b]):
        raise ScoreError("RUN-MARGIN: supply equal, nonempty lists of paired boolean outcomes")
    if not 0 <= decode_difference <= 1:
        raise ScoreError("SCORE-SPREAD: decode difference must be between zero and one")
    gain = sum(not x and y for x, y in zip(a, b))
    loss = sum(x and not y for x, y in zip(a, b))
    discordant = gain + loss
    pvalue = min(1.0, 2 * sum(math.comb(discordant, k) for k in range(min(gain, loss) + 1)) / 2**discordant)
    difference = (gain - loss) / len(a)
    lower, upper = paired_interval(a, b)
    result = "not shown"
    if pvalue < 0.05 and abs(difference) > decode_difference:
        result = "higher" if gain > loss else "lower"
    elif lower >= -0.05 and upper <= 0.05:
        result = "flat"
    return {"result": result, "b": gain, "c": loss, "pairs": len(a), "p": pvalue,
            "difference": difference, "interval_95": [lower, upper], "decode_difference": decode_difference,
            "limit": "On 100 segments, differences below about 10 points often remain not shown."}


def review_bundle(plan, runs, prompt_path, *, seed=0):
    """SCORE-REVIEW: blind all compared procedures together with one fixed segment sample."""
    items, mapping = [], {}
    prompts = {entry["id"]: entry["prompt"] for entry in plan["entries"]}
    for name, results in runs.items():
        by_id = {result["id"]: result for result in results}
        for segment in plan["review_sample"]:
            items.append((name, segment, {"prompt": prompts[segment], "output": by_id[segment]["output"]}))
    random.Random(seed).shuffle(items)
    blinded = []
    for index, (name, segment, item) in enumerate(items):
        key = f"review-{index + 1:04d}"
        blinded.append({"id": key, **item})
        mapping[key] = {"run": name, "segment": segment}
    return {"model": REVIEW_MODEL, "prompt_sha256": mf.sha256_file(prompt_path),
            "count_per_run": len(plan["review_sample"]), "items": blinded}, mapping


def apply_review(bundle, mapping, verdicts, overrides=()):
    """SCORE-REVIEW: require every blinded verdict and mark operator replacements."""
    decisions = {item["id"]: item for item in verdicts}
    if bundle.get("model") != REVIEW_MODEL or {item["id"] for item in bundle["items"]} != set(mapping):
        raise ScoreError("SCORE-REVIEW: model or blinded item mapping differs")
    if set(decisions) != set(mapping) or len(decisions) != len(verdicts):
        raise ScoreError("SCORE-REVIEW: decisions must cover each blinded item exactly once")
    for key, item in decisions.items():
        if item.get("decision") not in ("fits", "odd", "incorrect"):
            raise ScoreError(f"SCORE-REVIEW: invalid decision for {key}")
    decisions = {key: {**item, "operator_override": False} for key, item in decisions.items()}
    if len({item["id"] for item in overrides}) != len(overrides):
        raise ScoreError("SCORE-REVIEW: each operator replacement must name a distinct item")
    for item in overrides:
        if item["id"] not in decisions or item.get("decision") not in ("fits", "odd", "incorrect"):
            raise ScoreError("SCORE-REVIEW: invalid operator replacement")
        decisions[item["id"]] = {**item, "operator_override": True,
                                  "model_decision": decisions[item["id"]]["decision"]}
    return {"model": bundle["model"], "prompt_sha256": bundle["prompt_sha256"],
            "decisions": [{**item, **mapping[key]} for key, item in decisions.items()]}


def score_model(plan_path, build_path, out_dir, *, model_name, prompt_path, adapter=None):
    """SCORE-GENERATE, SCORE-OUTPUT: greedy decode on the verified split model."""
    import torch

    model_manifest_path = loader.manifest_path_for(model_name)
    model_dir = loader.resolve_dir(mf.load_tree(model_manifest_path)).resolve()
    out_dir = out_dir.resolve()
    # SCORE-OUTPUT: keep score files outside the read-only reference tree in either direction.
    if out_dir == model_dir or model_dir in out_dir.parents or out_dir in model_dir.parents:
        raise ScoreError("SCORE-OUTPUT: output overlaps the reference model directory")
    if out_dir.exists():
        raise ScoreError("SCORE-OUTPUT: each score needs a new output directory")
    plan = json.loads(plan_path.read_text())
    record = json.loads(build_path.read_text())
    for field in FIELDS:
        if (field not in plan or field not in record or type(plan[field]) is not type(record[field])
                or plan[field] != record[field]):
            raise ScoreError(f"RECONCILE-FIELDS: plan differs on {field}")
    loaded = loader.load(model_name, path=model_dir, manifest_path=model_manifest_path, device_map="balanced")
    validate_build(record, loaded.tokenizer, loaded.tree_hash, plan["max_seq_length"])
    model = loaded.model
    if any(parameter.device.type != "cuda" for parameter in model.parameters()):
        raise ScoreError("SCORE-GENERATE: model has a parameter off GPU")
    adapter_hash = "none"
    if adapter is not None:
        adapter_manifest = mf.load_tree(adapter.parent / f"{adapter.name}.manifest.json")
        adapter_hash = mf.require_tree(adapter_manifest, adapter)
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, str(adapter), is_trainable=False, local_files_only=True)
        loader.pin_layer_devices(model)
    model.eval()
    tok = loaded.tokenizer
    end_token = tok.convert_tokens_to_ids("<|im_end|>")
    if end_token is None or end_token == tok.unk_token_id:
        raise ScoreError("SCORE-GENERATE: end-of-turn token is unavailable")
    judge, checker_hash = load_checker()
    out_dir.mkdir(parents=True)
    score = {"engine": "python-hf", "tree_hash": loaded.tree_hash, "adapter_sha256": adapter_hash,
             **{field: record[field] for field in FIELDS}, "plan_sha256": mf.sha256_file(plan_path),
             "build_sha256": mf.sha256_file(build_path), "status": "started",
             "checker_prompt_sha256": checker_hash,
             "reviewer_model": REVIEW_MODEL, "reviewer_prompt_sha256": mf.sha256_file(prompt_path),
             "review_sample": plan["review_sample"], "review_count": plan["review_count"],
             "review_status": "pending", "excluded": plan["excluded"], "request_set": plan["request_set"],
             "sampler": {"do_sample": False, "num_beams": 1, "max_new_tokens": OUTPUT_LIMIT,
                         "eos_token_id": end_token}, "commit": mf.git_commit()}
    (out_dir / "score.json").write_text(json.dumps(score, indent=2) + "\n")
    started = time.monotonic()
    results = []
    for device in range(torch.cuda.device_count()):
        torch.cuda.reset_peak_memory_stats(device)
    try:
        with (out_dir / "segments.jsonl").open("w", encoding="utf-8") as stream:
            for entry in plan["entries"]:
                expected = build.render_row(tok, entry["prompt"], enable_thinking=False,
                                             add_generation_prompt=True)
                if expected != entry["input_ids"] or len(expected) + OUTPUT_LIMIT > plan["max_seq_length"]:
                    raise ScoreError(f"SCORE-PREFIX: frozen prompt differs for {entry['id']}")
                ids = torch.tensor([expected], dtype=torch.long, device=model.get_input_embeddings().weight.device)
                with torch.inference_mode():
                    output = model.generate(input_ids=ids, attention_mask=torch.ones_like(ids),
                                            do_sample=False, num_beams=1, max_new_tokens=OUTPUT_LIMIT,
                                            eos_token_id=end_token,
                                            pad_token_id=tok.pad_token_id if tok.pad_token_id is not None else end_token)
                generated = output[0, ids.shape[1]:].tolist()
                if generated and generated[-1] == end_token:
                    generated.pop()
                text = tok.decode(generated, skip_special_tokens=False)
                result = evaluate_output(entry, text, judge)
                results.append(result)
                stream.write(json.dumps(result) + "\n")
                stream.flush()
                print(f"scored {len(results)}/{len(plan['entries'])}: {entry['id']}", flush=True)
        passed = sum(result["check"]["ok"] for result in results)
        correct = sum(result["correct_request"] is True for result in results)
        score["totals"] = {"scored": len(results), "ste_pass": passed,
                           "ste_pass_rate": passed / len(results) if results else None,
                           "requests": len(plan["request_set"]), "correct_requests": correct,
                           "correct_request_rate": correct / len(plan["request_set"]) if plan["request_set"] else None,
                           "word_count": sum(result["word_count"] for result in results),
                           "ing_words": sum(result["ing_words"] for result in results)}
        score["status"] = "completed"
    except BaseException as exc:
        score.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        score["wall_seconds"] = time.monotonic() - started
        score["peak_vram_gib"] = {f"cuda:{i}": torch.cuda.max_memory_allocated(i) / 2**30
                                  for i in range(torch.cuda.device_count())}
        (out_dir / "score.json").write_text(json.dumps(score, indent=2) + "\n")
    return score


def read_score(directory, plan_path):
    """SCORE-OUTPUT, SCORE-VALIDITY: only complete scores of the same frozen plan can compare."""
    record = json.loads((directory / "score.json").read_text())
    plan = json.loads(plan_path.read_text())
    if record.get("status") != "completed" or record.get("engine") != "python-hf":
        raise ScoreError("SCORE-OUTPUT: score is incomplete or from a different engine")
    if record.get("plan_sha256") != mf.sha256_file(plan_path):
        raise ScoreError("SCORE-VALIDITY: score has a different evaluation plan")
    rows = build.read_jsonl(directory / "segments.jsonl")
    if [row["id"] for row in rows] != [entry["id"] for entry in plan["entries"]]:
        raise ScoreError("SCORE-OUTPUT: scored segment set differs from the frozen plan")
    if record.get("request_set") != plan["request_set"]:
        raise ScoreError("SCORE-VALIDITY: request set differs from the frozen plan")
    for row, entry in zip(rows, plan["entries"]):
        if type(row["check"]["ok"]) is not bool:
            raise ScoreError("SCORE-CHECK: each segment needs a boolean checker result")
        if (entry["request"] and type(row["correct_request"]) is not bool
                or not entry["request"] and row["correct_request"] is not None):
            raise ScoreError("SCORE-VALIDITY: each request-set segment needs a boolean result")
    for field in FIELDS:
        if type(record.get(field)) is not type(plan[field]) or record[field] != plan[field]:
            raise ScoreError(f"RECONCILE-FIELDS: score differs on {field}")
    return record, rows


def review_run_identity(directory, record):
    """SCORE-REVIEW: bind verdicts to the completed run and the outputs that were reviewed."""
    if record.get("status") != "completed" or record.get("engine") != "python-hf":
        raise ScoreError("SCORE-REVIEW: only completed python-hf scores can receive verdicts")
    original = {key: value for key, value in record.items() if key not in ("review_status", "review_decisions")}
    digest = hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()
    return {"adapter_sha256": record["adapter_sha256"], "plan_sha256": record["plan_sha256"],
            "score_sha256": digest, "segments_sha256": mf.sha256_file(directory / "segments.jsonl")}


def compare_scores(plan_path, a_dir, b_dir, r0_first, r0_second):
    """SCORE-SPREAD, RUN-MARGIN: measure baseline decode spread separately for each rate."""
    records = [read_score(path, plan_path) for path in (a_dir, b_dir, r0_first, r0_second)]
    if any(record["adapter_sha256"] != "none" for record, _ in records[2:]):
        raise ScoreError("SCORE-SPREAD: both spread records must use the untrained model")
    results = {}
    for metric in ("ste", "correct_request"):
        outcomes = []
        for _, rows in records:
            outcomes.append([row["check"]["ok"] for row in rows] if metric == "ste" else
                            [row["correct_request"] for row in rows if row["correct_request"] is not None])
        a, b, first, second = outcomes
        spread = abs(sum(first) - sum(second)) / len(first) if first else 0
        results[metric] = compare_pairs(a, b, spread) if a else {"result": "not shown", "pairs": 0}
    return results


def stream_rows(path):
    """SCORE-LOCATE: scan the frozen source without holding the complete dataset in memory."""
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def main(argv=None):
    """SCORE clauses: plan, decode, compare and export or ingest blinded review records."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan_cmd = commands.add_parser("plan")
    plan_cmd.add_argument("--kit", type=Path, required=True)
    plan_cmd.add_argument("--dataset", type=Path, required=True)
    plan_cmd.add_argument("--build", type=Path, required=True)
    plan_cmd.add_argument("--out", type=Path, required=True)
    plan_cmd.add_argument("--model", default="nemotron-3-nano-30b-a3b")
    plan_cmd.add_argument("--seed", type=int, default=0)
    score_cmd = commands.add_parser("score")
    score_cmd.add_argument("--plan", type=Path, required=True)
    score_cmd.add_argument("--build", type=Path, required=True)
    score_cmd.add_argument("--out", type=Path, required=True)
    score_cmd.add_argument("--model", default="nemotron-3-nano-30b-a3b")
    score_cmd.add_argument("--review-prompt", type=Path, required=True)
    score_cmd.add_argument("--adapter", type=Path)
    compare = commands.add_parser("compare")
    compare.add_argument("--plan", type=Path, required=True)
    for name in ("a", "b", "r0-first", "r0-second"):
        compare.add_argument("--" + name, type=Path, required=True)
    export = commands.add_parser("review-export")
    export.add_argument("--plan", type=Path, required=True)
    export.add_argument("--score", type=Path, action="append", required=True)
    export.add_argument("--prompt", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--seed", type=int, default=0)
    ingest = commands.add_parser("review-import")
    ingest.add_argument("--bundle", type=Path, required=True)
    ingest.add_argument("--mapping", type=Path, required=True)
    ingest.add_argument("--verdicts", type=Path, required=True)
    ingest.add_argument("--overrides", type=Path)
    ingest.add_argument("--score", type=Path, action="append", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            if args.out.exists():
                raise ScoreError("SCORE-REVIEW: plan is frozen and cannot be overwritten")
            record = json.loads(args.build.read_text())
            model_manifest = mf.load_tree(loader.manifest_path_for(args.model))
            model_dir = loader.resolve_dir(model_manifest)
            mf.require_tree(model_manifest, model_dir)
            from transformers import AutoTokenizer
            tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False)
            validate_build(record, tokenizer, model_manifest.tree_hash, record["max_seq_length"])
            plan = make_plan(tokenizer, build.read_jsonl(args.kit), stream_rows(args.dataset), record, seed=args.seed)
            plan.update(kit_sha256=mf.sha256_file(args.kit), build_sha256=mf.sha256_file(args.build))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(plan) + "\n")
            print(json.dumps({"eligible": len(plan["entries"]), "excluded": plan["excluded"],
                              "review_count": plan["review_count"]}))
        elif args.command == "score":
            print(json.dumps(score_model(args.plan, args.build, args.out, model_name=args.model,
                                          prompt_path=args.review_prompt, adapter=args.adapter), indent=2))
        elif args.command == "compare":
            print(json.dumps(compare_scores(args.plan, args.a, args.b, args.r0_first, args.r0_second), indent=2))
        elif args.command == "review-export":
            if args.out.exists() or len({path.name for path in args.score}) != len(args.score):
                raise ScoreError("SCORE-REVIEW: use a fresh directory and unique score directory names")
            plan = json.loads(args.plan.read_text())
            records = {path.name: read_score(path, args.plan) for path in args.score}
            if any(record["reviewer_prompt_sha256"] != mf.sha256_file(args.prompt)
                   for record, _ in records.values()):
                raise ScoreError("SCORE-REVIEW: prompt hash differs from the scores")
            runs = {name: rows for name, (_, rows) in records.items()}
            bundle, mapping = review_bundle(plan, runs, args.prompt, seed=args.seed)
            identities = {path.name: review_run_identity(path, records[path.name][0]) for path in args.score}
            bundle_text = json.dumps(bundle) + "\n"
            private = {"items": mapping, "runs": identities,
                       "bundle_sha256": hashlib.sha256(bundle_text.encode()).hexdigest()}
            args.out.mkdir(parents=True)
            (args.out / "blinded.json").write_text(bundle_text)
            (args.out / "mapping.json").write_text(json.dumps(private) + "\n")
        else:
            bundle = json.loads(args.bundle.read_text())
            mapping = json.loads(args.mapping.read_text())
            if mapping.get("bundle_sha256") != mf.sha256_file(args.bundle):
                raise ScoreError("SCORE-REVIEW: mapping belongs to a different blinded bundle")
            reviewed = apply_review(bundle, mapping["items"], build.read_jsonl(args.verdicts),
                                    build.read_jsonl(args.overrides) if args.overrides else [])
            names = [path.name for path in args.score]
            if (len(set(names)) != len(names) or set(names) != set(mapping["runs"])
                    or set(names) != {item["run"] for item in reviewed["decisions"]}):
                raise ScoreError("SCORE-REVIEW: score directories differ from the blinded runs")
            updates = []
            for path in args.score:
                record = json.loads((path / "score.json").read_text())
                if review_run_identity(path, record) != mapping["runs"][path.name]:
                    raise ScoreError("SCORE-REVIEW: score record or outputs differ from the exported run")
                if (record.get("reviewer_prompt_sha256") != reviewed["prompt_sha256"]
                        or record.get("reviewer_model") != reviewed["model"]):
                    raise ScoreError("SCORE-REVIEW: reviewer model or prompt hash differs from the score record")
                decisions = [item for item in reviewed["decisions"] if item["run"] == path.name]
                if {item["segment"] for item in decisions} != set(record["review_sample"]):
                    raise ScoreError("SCORE-REVIEW: review set differs from the score record")
                record.update(review_status="completed", review_decisions=decisions)
                updates.append((path, record))
            for path, record in updates:
                (path / "score.json").write_text(json.dumps(record, indent=2) + "\n")
    except (ScoreError, mf.ManifestError, OSError, KeyError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
