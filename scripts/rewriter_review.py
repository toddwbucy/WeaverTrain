"""Spec 0.2 REWRITE-REVIEW: prepare blind inputs and retain external verdicts.

This command makes no reviewer call. Reviewer files are evaluation evidence only.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import rewriter_report as report

score = report.score
mf = score.mf
require = score.require
REPO = score.REPO
VERDICTS = ("faithful", "minor drift", "wrong", "data altered")


def json_bytes(value):
    """REWRITE-RECORD: retain JSON with an explicit byte identity."""
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode()


def read_sources(plan_dir, sources, repo):
    """REWRITE-REVIEW: verify the frozen plan, prompt and all supplied scores."""
    bundle = score.read_plan(plan_dir, repo=repo)
    prompt = (repo / "config/ste-rewrite-review-prompt.md").read_bytes()
    require(score.build.digest(prompt) == bundle[0]["reviewer_prompt_sha256"],
            "REWRITE-REVIEW: review prompt bytes differ")
    runs = [report.read_score(path, bundle, adapter=adapter,
            training_build=training, repo=repo) for path, adapter, training in sources]
    require(bool(runs), "REWRITE-REVIEW: at least one score is required")
    identities = [run["record"]["run_id"] for run in runs]
    require(len(set(identities)) == len(identities),
            "REWRITE-REVIEW: score identity repeats")
    cohorts = [(run["record"]["procedure"], run["record"]["epoch"]) for run in runs]
    require(len(set(cohorts)) == len(cohorts),
            "REWRITE-REVIEW: procedure and epoch repeat")
    runs.sort(key=lambda run: (run["record"]["procedure"], run["record"]["epoch"]))
    return bundle, prompt, runs


def sequence(bundle, runs, batch_id):
    """REWRITE-REVIEW: restore safe first rewrites and shuffle the fixed sample."""
    require(isinstance(batch_id, str) and UUID(batch_id).hex == batch_id,
            "REWRITE-REVIEW: batch identity must be a UUID")
    plan, kit = bundle[:2]
    selected = set(plan["review_sample"])
    sources = {row["id"]: row for row in kit["rows"]}
    items, unavailable = [], []
    for run in runs:
        record = run["record"]
        for check in run["checks"]:
            key = check["id"]
            if key not in selected:
                continue
            source = sources[key]
            masked = check["output"]
            identity = {"run_id": record["run_id"], "procedure": record["procedure"],
                        "epoch": record["epoch"], "source_id": key,
                        "checker_accepted": check["check"]["ok"],
                        "finish_reason": check["raw"]["finish_reason"]}
            reasons = []
            if not masked:
                reasons.append("missing_output")
            else:
                # REWRITE-REVIEW: the frozen placeholder rule also covers truncation.
                pattern = score.ste_check.PLACEHOLDER
                if sorted(pattern.findall(masked)) != sorted(pattern.findall(source["text"])):
                    reasons.append("placeholder_failure")
                if check["literal_failures"]:
                    reasons.append("literal_failure")
            if reasons:
                unavailable.append({**identity, "reasons": reasons})
                continue
            restored = score.ste_convert.unmask(source["text"], source["codes"])
            require(restored == source["raw"],
                    "REWRITE-REVIEW: source code restoration differs")
            items.append({**identity, "source": restored,
                          "rewrite": score.ste_convert.unmask(masked, source["codes"]),
                          "masked_source": source["text"], "masked_rewrite": masked,
                          "codes": source["codes"]})
    # REWRITE-REVIEW: sort the compared procedures before the fixed shuffle.
    items.sort(key=lambda item: (item["procedure"], item["epoch"], item["source_id"]))
    random.Random(2032).shuffle(items)
    blind, mapping = [], []
    for n, item in enumerate(items):
        review_id = f"{batch_id}-{n:03d}"
        blind.append({"review_id": review_id, "source": item["source"],
                      "rewrite": item["rewrite"]})
        mapping.append({"review_id": review_id, **item})
    return blind, mapping, unavailable


def payload(plan_dir, sources, batch_id, repo):
    """REWRITE-REVIEW: derive the exact input and private map from verified records."""
    bundle, prompt, runs = read_sources(plan_dir, sources, repo)
    blind, mapping, unavailable = sequence(bundle, runs, batch_id)
    raw = score.jsonl_bytes(blind)
    audit = {"batch_id": batch_id, "plan_tree_hash": bundle[4],
             "plan_manifest_sha256": bundle[5], "model": score.REVIEW_MODEL,
             "prompt_sha256": score.build.digest(prompt),
             "input_sha256": score.build.digest(raw), "shuffle_seed": 2032,
             "sample": bundle[0]["review_sample"],
             "runs": [run["identity"] for run in runs],
             "sequence": mapping, "unreviewed": unavailable}
    return prompt, raw, audit


def input_paths(plan_dir, sources):
    """REWRITE-RECORD: protect every source tree from output overlap."""
    return [plan_dir, *[path for row in sources for path in row if path is not None]]


def save_record(out, name, record):
    """REWRITE-RECORD: keep a failure record without a success manifest."""
    (out / name).write_bytes(json_bytes(record))


def seal(out, manifest_path, name):
    """REWRITE-RECORD: seal completed artifacts beside their output tree."""
    manifest = mf.build_tree(out, "evaluation", name)
    with manifest_path.open("x", encoding="utf-8") as stream:
        stream.write(manifest.to_json())
    return manifest


def prepare(plan_dir, sources, out_dir, *, repo=REPO):
    """REWRITE-REVIEW: each preparation gets a fresh identity and private audit map."""
    sources = list(sources)
    out, manifest_path = score.fresh_score_output(out_dir, repo=repo,
        inputs=input_paths(plan_dir, sources))
    record = {"schema": "weaver-train/rewriter-review-batch/v1",
              "batch_id": uuid4().hex, "status": "started",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "commit": mf.git_commit(repo)}
    out.mkdir(parents=True, exist_ok=False)
    save_record(out, "batch.json", record)
    try:
        prompt, raw, audit = payload(plan_dir, sources, record["batch_id"], repo)
        reviewer = out / "reviewer"
        reviewer.mkdir()
        (reviewer / "prompt.md").write_bytes(prompt)
        (reviewer / "input.jsonl").write_bytes(raw)
        (out / "private.json").write_bytes(json_bytes(audit))
        require(payload(plan_dir, sources, record["batch_id"], repo) == (prompt, raw, audit),
                "REWRITE-RECORD: review sources changed during preparation")
        record.update(status="completed", input_sha256=audit["input_sha256"],
                      prompt_sha256=audit["prompt_sha256"],
                      private_sha256=score.build.digest(json_bytes(audit)),
                      available=len(audit["sequence"]), unreviewed=len(audit["unreviewed"]))
        save_record(out, "batch.json", record)
        manifest = seal(out, manifest_path, "ste-rewriter-review-batch")
    except BaseException as exc:
        record.update(status="failed", failure={"type": type(exc).__name__, "message": str(exc)})
        save_record(out, "batch.json", record)
        raise
    return record, manifest


def read_batch(batch_dir, plan_dir, sources, repo):
    """REWRITE-REVIEW: reconstruct sequence and map before accepting verdicts."""
    root, manifest, manifest_sha = score.artifact(batch_dir)
    require(manifest.kind == "evaluation" and manifest.name == "ste-rewriter-review-batch",
            "REWRITE-REVIEW: expected a review batch manifest")
    require({entry.path for entry in manifest.files} ==
            {"batch.json", "private.json", "reviewer/input.jsonl", "reviewer/prompt.md"},
            "REWRITE-REVIEW: review batch file set differs")
    captured = {name: score.captured_file(root, manifest, name) for name in
                ("batch.json", "private.json", "reviewer/input.jsonl", "reviewer/prompt.md")}
    record = json.loads(captured["batch.json"])
    require(record["schema"] == "weaver-train/rewriter-review-batch/v1" and
            record["status"] == "completed" and "failure" not in record,
            "REWRITE-REVIEW: a completed review batch is required")
    prompt, raw, audit = payload(plan_dir, sources, record["batch_id"], repo)
    require(captured["reviewer/prompt.md"] == prompt,
            "REWRITE-REVIEW: retained review prompt differs")
    require(captured["reviewer/input.jsonl"] == raw,
            "REWRITE-REVIEW: retained blind sequence differs")
    require(captured["private.json"] == json_bytes(audit),
            "REWRITE-REVIEW: retained private map differs")
    require(record["input_sha256"] == audit["input_sha256"] and
            record["prompt_sha256"] == audit["prompt_sha256"] and
            record["private_sha256"] == score.build.digest(captured["private.json"]) and
            record["available"] == len(audit["sequence"]) and
            record["unreviewed"] == len(audit["unreviewed"]),
            "REWRITE-REVIEW: batch summary differs")
    return audit, {"tree_hash": manifest.tree_hash, "manifest_sha256": manifest_sha,
                   "batch_sha256": score.build.digest(captured["batch.json"])}


def validate_verdict(value, item):
    """REWRITE-REVIEW: require one defined verdict with distinct quoted defects."""
    require(set(value) == {"review_id", "verdict", "defects"} and
            value["review_id"] == item["review_id"] and value["verdict"] in VERDICTS and
            isinstance(value["defects"], list),
            "REWRITE-REVIEW: invalid verdict fields")
    require((value["verdict"] == "faithful") == (len(value["defects"]) == 0),
            "REWRITE-REVIEW: verdict and defect count disagree")
    seen = set()
    for defect in value["defects"]:
        require(isinstance(defect, dict) and
                set(defect) == {"source_quote", "rewrite_quote", "reason"} and
                all(isinstance(text, str) for text in defect.values()) and
                bool(defect["reason"].strip()),
                "REWRITE-REVIEW: invalid defect fields")
        require(bool(defect["source_quote"].strip()),
                "REWRITE-REVIEW: source quote is empty")
        require(defect["source_quote"] in item["source"] and
                defect["rewrite_quote"] in item["rewrite"],
                "REWRITE-REVIEW: defect quote differs from reviewed text")
        key = tuple(defect[name] for name in ("source_quote", "rewrite_quote", "reason"))
        require(key not in seen, "REWRITE-REVIEW: duplicate defect")
        seen.add(key)


def summarize(rows):
    """REWRITE-REVIEW: keep reviewed and checker-accepted denominators separate."""
    counts = Counter(row["effective"]["verdict"] for row in rows)
    accepted = [row for row in rows if row["checker_accepted"]]
    defects = sum(len(row["effective"]["defects"]) for row in accepted)
    affected = sum(bool(row["effective"]["defects"]) for row in accepted)
    denominator = len(accepted)
    return {"verdicts": {name: counts[name] for name in VERDICTS},
            "reviewed": len(rows), "reviewed_checker_accepted": denominator,
            "distinct_defects": defects,
            "defects_per_100": 100 * defects / denominator if denominator else None,
            "segments_with_defects": affected,
            "segments_with_defects_rate": affected / denominator if denominator else None}


def ingest(batch_dir, plan_dir, sources, verdict_file, out_dir, *, input_sha256,
           prompt_sha256, verdicts_sha256, overrides=None, repo=REPO):
    """REWRITE-REVIEW: preserve original bytes and dated overrides in a new record."""
    sources = list(sources)
    inputs = input_paths(plan_dir, sources) + [batch_dir, verdict_file]
    if overrides is not None:
        inputs.append(overrides)
    out, manifest_path = score.fresh_score_output(out_dir, repo=repo, inputs=inputs)
    record = {"schema": "weaver-train/rewriter-review-verdicts/v1",
              "record_id": uuid4().hex, "status": "started", "completed_ids": [],
              "created_at": datetime.now(timezone.utc).isoformat(),
              "commit": mf.git_commit(repo), "model": score.REVIEW_MODEL,
              "deposited_verdicts_sha256": verdicts_sha256}
    out.mkdir(parents=True, exist_ok=False)
    save_record(out, "review.json", record)
    try:
        audit, identity = read_batch(batch_dir, plan_dir, sources, repo)
        record.update(batch_id=audit["batch_id"], batch=identity,
                      unreviewed=audit["unreviewed"],
                      pending_review_ids=[item["review_id"] for item in audit["sequence"]])
        require(input_sha256 == audit["input_sha256"] and
                prompt_sha256 == audit["prompt_sha256"],
                "REWRITE-REVIEW: submitted reviewer input or prompt hash differs")
        raw = Path(verdict_file).read_bytes()
        (out / "original-verdicts.jsonl").write_bytes(raw)
        record.update(verdicts_sha256=score.build.digest(raw),
                      input_sha256=input_sha256, prompt_sha256=prompt_sha256)
        require(record["verdicts_sha256"] == verdicts_sha256,
                "REWRITE-REVIEW: verdict file hash differs from deposited hash")
        verdicts = report.rows(raw)
        items = audit["sequence"]
        require([row["review_id"] for row in verdicts] ==
                [item["review_id"] for item in items],
                "REWRITE-REVIEW: verdict IDs differ from blind input order")
        raw_overrides = Path(overrides).read_bytes() if overrides is not None else b""
        (out / "overrides.jsonl").write_bytes(raw_overrides)
        record["overrides_sha256"] = score.build.digest(raw_overrides)
        override_map = {}
        for override in report.rows(raw_overrides):
            require(set(override) == {"review_id", "verdict", "defects", "operator", "date"},
                    "REWRITE-REVIEW: invalid override fields")
            key = override["review_id"]
            require(key in {item["review_id"] for item in items} and key not in override_map,
                    "REWRITE-REVIEW: duplicate or unknown override ID")
            require(isinstance(override["operator"], str) and override["operator"].strip() and
                    isinstance(override["date"], str) and
                    date.fromisoformat(override["date"]).isoformat() == override["date"],
                    "REWRITE-REVIEW: override needs an operator and absolute date")
            override_map[key] = override
        rows = []
        for item, verdict in zip(items, verdicts):
            validate_verdict(verdict, item)
            override = override_map.get(item["review_id"])
            effective = verdict if override is None else {
                key: override[key] for key in ("review_id", "verdict", "defects")}
            validate_verdict(effective, item)
            rows.append({key: item[key] for key in
                         ("review_id", "run_id", "procedure", "epoch", "source_id",
                          "checker_accepted")})
            rows[-1].update(original=verdict, override=override, effective=effective)
            record["completed_ids"].append(item["review_id"])
            record["pending_review_ids"].remove(item["review_id"])
        record.update(summary=summarize(rows), by_run={key: summarize([
            row for row in rows if row["run_id"] == key]) for key in
            [run["run_id"] for run in audit["runs"]]})
        require(read_batch(batch_dir, plan_dir, sources, repo) == (audit, identity),
                "REWRITE-RECORD: review batch changed during ingestion")
        require(Path(verdict_file).read_bytes() == raw and
                (overrides is None or Path(overrides).read_bytes() == raw_overrides),
                "REWRITE-RECORD: verdict input changed during ingestion")
        (out / "verdicts.jsonl").write_bytes(score.jsonl_bytes(rows))
        record["status"] = "completed"
        save_record(out, "review.json", record)
        manifest = seal(out, manifest_path, "ste-rewriter-review-verdicts")
    except BaseException as exc:
        record.update(status="failed", failure={"type": type(exc).__name__, "message": str(exc)})
        save_record(out, "review.json", record)
        raise
    return record, manifest


def main(argv=None, *, repo=REPO):
    """REWRITE-REVIEW: prepare or ingest local files without invoking a reviewer."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "ingest"):
        command = commands.add_parser(name)
        command.add_argument("--plan", type=Path, required=True)
        command.add_argument("--run", type=Path, action="append", default=[])
        command.add_argument("--candidate", type=Path, nargs=3, action="append", default=[])
        command.add_argument("--out", type=Path, required=True)
        if name == "ingest":
            command.add_argument("--batch", type=Path, required=True)
            command.add_argument("--verdicts", type=Path, required=True)
            command.add_argument("--input-sha256", required=True)
            command.add_argument("--prompt-sha256", required=True)
            command.add_argument("--verdicts-sha256", required=True)
            command.add_argument("--overrides", type=Path)
    args = parser.parse_args(argv)
    sources = [(path, None, None) for path in args.run] + args.candidate
    try:
        if args.command == "prepare":
            record, manifest = prepare(args.plan, sources, args.out, repo=repo)
        else:
            record, manifest = ingest(args.batch, args.plan, sources, args.verdicts,
                args.out, input_sha256=args.input_sha256, prompt_sha256=args.prompt_sha256,
                verdicts_sha256=args.verdicts_sha256, overrides=args.overrides, repo=repo)
    except (mf.ManifestError, OSError, KeyError, TypeError, IndexError, ValueError,
            RuntimeError) as exc:
        print(f"review: stopped: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": record["status"], "tree_hash": manifest.tree_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
