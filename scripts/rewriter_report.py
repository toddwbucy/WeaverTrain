"""Spec 0.2 REWRITE-SPREAD and REWRITE-RECORD: verify runs and retain comparisons."""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import rewriter_score as score

mf = score.mf
require = score.require
REPO = score.REPO


def rows(raw):
    """REWRITE-RECORD: read captured JSONL bytes without changing row order."""
    return [json.loads(line) for line in raw.splitlines()]


def verify_adapter(record, adapter, training_build, plan, repo):
    """REWRITE-SPREAD: compare the saved score identity with its producer artifacts."""
    root, manifest, manifest_sha = score.artifact(adapter)
    require(manifest.kind == "model", "REWRITE-DECODE: model adapter required")
    proof = score.adapter_producer(root, manifest, training_build,
        record["procedure"], record["epoch"], plan, repo)
    require(record["adapter"] == {"tree_hash": manifest.tree_hash,
            "manifest_sha256": manifest_sha, "producer": proof},
            "REWRITE-SPREAD: score adapter identity differs from its producer")


def verify_snapshot(directory, identity):
    """REWRITE-RECORD: reject source changes before sealing a comparison."""
    _, after, after_sha = score.artifact(directory)
    require(after.tree_hash == identity["tree_hash"] and
            after_sha == identity["manifest_sha256"],
            "REWRITE-RECORD: score artifact changed while reading")


def read_score(directory, plan_bundle, *, adapter=None, training_build=None, repo=REPO):
    """REWRITE-SPREAD: establish score and producer identities before comparison."""
    plan, kit, entries, folded, plan_tree, plan_sha = plan_bundle
    root, manifest, manifest_sha = score.artifact(directory)
    require(manifest.kind == "evaluation" and manifest.name == "ste-rewriter-run",
            "REWRITE-RECORD: expected a rewriter score manifest")
    run_bytes = score.captured_file(root, manifest, "run.json")
    record = json.loads(run_bytes)
    require(record["schema"] == "weaver-train/rewriter-run/v1" and
            record["status"] == "completed" and "failure" not in record,
            "REWRITE-RECORD: comparisons require completed score records")
    score.require_fields(record, plan)
    expected = {"engine": "python-hf", "precision": "bf16",
                "model": score.build.MODEL, "device_map": "balanced",
                "plan_tree_hash": plan_tree, "plan_manifest_sha256": plan_sha,
                **{key: plan[key] for key in ("environment_sha256", "build_sha256",
                    "input_sha256", "folded_prompt", "prompt_provenance", "sampler")}}
    require(all(record[key] == value for key, value in expected.items()),
            "REWRITE-SPREAD: score identities differ from the frozen plan")
    ids = [entry["id"] for entry in entries]
    require(record["completed_ids"] == ids and record["exclusions"] == [],
            "REWRITE-KIT: the score must cover all 100 frozen inputs")
    require(isinstance(record["run_id"], str) and record["run_id"],
            "REWRITE-RECORD: a score run identity is required")
    created = datetime.fromisoformat(record["created_at"])
    require(created.utcoffset() is not None,
            "REWRITE-RECORD: score time needs a UTC offset")
    seconds = record["wall_seconds"]
    require(type(seconds) in (int, float) and math.isfinite(seconds) and seconds >= 0,
            "REWRITE-RECORD: a finite nonnegative wall time is required")
    require(set(record["gpus"]) == set(record["peak_memory_bytes"]) == {"0", "1"},
            "REWRITE-RECORD: both GPU identities and peaks are required")
    for gpu in ("0", "1"):
        for key in ("allocated", "reserved"):
            value = record["peak_memory_bytes"][gpu][key]
            require(type(value) is int and value >= 0,
                    "REWRITE-RECORD: nonnegative integer memory peaks required")
    require(record["review"] == {"status": "pending", "model": plan["reviewer_model"],
            "prompt_sha256": plan["reviewer_prompt_sha256"],
            "sample": plan["review_sample"]},
            "REWRITE-REVIEW: frozen review selection differs")
    procedure, epoch = record["procedure"], record["epoch"]
    require(type(epoch) is int, "REWRITE-CONFIG: integer epoch required")
    if procedure == "r0":
        require(epoch == 0 and record["adapter"] is None and adapter is None and
                training_build is None, "REWRITE-SPREAD: R0 must be untrained")
    else:
        require(procedure in ("r1", "r2") and epoch in (1, 2) and
                adapter is not None and training_build is not None,
                "REWRITE-SPREAD: adapter comparisons need the producer artifacts")
        verify_adapter(record, adapter, training_build, plan, repo)
    segment_bytes = score.captured_file(root, manifest, "segments.jsonl")
    require(score.build.digest(segment_bytes) == record["segments_sha256"],
            "REWRITE-RECORD: segment hash differs from score record")
    outputs = rows(segment_bytes)
    require([row["id"] for row in outputs] == ids,
            "REWRITE-KIT: raw output identities differ from the frozen plan")
    check_root, check_manifest, check_sha = score.artifact(root / "checks")
    require(check_manifest.kind == "evaluation" and
            check_manifest.name == "ste-rewriter-check",
            "REWRITE-CHECK: expected the frozen checker artifact")
    names = ("checks.jsonl", "checks-summary.json", "raw.jsonl", "input.jsonl",
             "output.jsonl", "score.md", score.build.PROMPT)
    captured = {name: score.captured_file(check_root, check_manifest, name)
                for name in names}
    # REWRITE-CHECK: rerun the frozen checker with explicit input and prompt files.
    with tempfile.TemporaryDirectory(prefix="weaver-rewriter-rescore-") as scratch:
        verified = Path(scratch) / "checks"
        summary, checks = score.check_outputs(kit, outputs, verified, repo=repo)
        require(all((verified / name).read_bytes() == raw
                    for name, raw in captured.items()),
                "REWRITE-CHECK: retained checks differ from the frozen scorer")
    lengths = [{"id": row["id"], "characters": row["output_characters"],
                "tokens": row["output_tokens"]} for row in checks]
    require(record["checks"] == summary and record["output_lengths"] == lengths,
            "REWRITE-RECORD: run summary differs from retained checker records")
    score.rewrite_outcomes(ids, checks)
    identity = {"run_id": record["run_id"], "tree_hash": manifest.tree_hash,
                "manifest_sha256": manifest_sha,
                "run_sha256": score.build.digest(run_bytes),
                "checks_manifest_sha256": check_sha}
    verify_snapshot(root, identity)
    if adapter is not None:
        verify_adapter(record, adapter, training_build, plan, repo)
    return {"record": record, "summary": summary, "checks": checks,
            "outputs": outputs, "identity": identity}


def run_summary(run):
    """REWRITE-SPREAD, REWRITE-RECORD: carry each run's complete measured breakdowns."""
    record = run["record"]
    return {"identity": run["identity"], "checks": run["summary"],
            **{key: record[key] for key in ("procedure", "epoch", "created_at",
                "commit", "adapter", "wall_seconds", "peak_memory_bytes", "gpus",
                "output_lengths", "review", "loop_checks")}}


def markdown(record):
    """REWRITE-SPREAD: show the measured rewrite result without a meaning claim."""
    lines = ["# Rewriter score comparison", "", f"Date: {record['created_at']}", "",
             "Metric: rewrite STE. Meaning review is recorded separately.", "",
             "Reference: the first R0 pass. Score denominator: 100.", "",
             "| Run | Accepted | Rate | Wall seconds |",
             "| --- | ---: | ---: | ---: |"]
    for label, run in record["runs"].items():
        checks = run["checks"]
        lines.append(f"| {label} | {checks['passed']}/100 | {checks['rate']:.2%} | "
                     f"{run['wall_seconds']:.3f} |")
    lines += ["", f"R0 absolute decode difference: {record['decode_difference']:.2%}.",
              "", "| Epoch score | Paired difference | 95% interval | p | Result |",
              "| --- | ---: | --- | ---: | --- |"]
    for label, result in record["comparisons"].items():
        value = result["comparison"]
        low, high = value["interval_95"]
        lines.append(f"| {label} | {value['difference']:.2%} | "
                     f"[{low:.2%}, {high:.2%}] | {value['p']:.6g} | "
                     f"{value['result']} |")
    lines += ["", "The interval and sign test describe these 100 segments only.",
              "The JSON record retains per-kind and per-bin totals, failure rules,",
              "output lengths, GPU peaks, wall times and artifact identities.",
              "These STE results do not establish a change in meaning quality.", "",
              f"Commit: {record['commit']['rev']}", ""]
    return "\n".join(lines)


def write_comparison(plan_dir, first, second, out_dir, *, candidates=(), repo=REPO):
    """REWRITE-SPREAD, REWRITE-RECORD: retain a fresh verified comparison or failure."""
    candidates = list(candidates)
    inputs = [plan_dir, first, second]
    for item in candidates:
        inputs.extend(item)
    out, manifest_path = score.fresh_score_output(out_dir, repo=repo, inputs=inputs)
    record = {"schema": "weaver-train/rewriter-comparison/v1", "run_id": uuid4().hex,
              "created_at": datetime.now(timezone.utc).isoformat(),
              "commit": mf.git_commit(repo), "status": "started",
              "metric": "rewrite_ste", "reference": "r0_first", "runs": {},
              "comparisons": {}, "completed_ids": [], "meaning_review": "pending"}
    out.mkdir(parents=True, exist_ok=False)

    def save():
        """REWRITE-RECORD: retain partial validation without reusing another record."""
        (out / "comparison.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    save()
    try:
        bundle = score.read_plan(plan_dir, repo=repo)
        record["plan_tree_hash"], record["plan_manifest_sha256"] = bundle[4:]
        verified, sources = [], []
        for path in (first, second):
            run = read_score(path, bundle, repo=repo)
            require(run["record"]["procedure"] == "r0",
                    "REWRITE-SPREAD: both references must be untrained R0")
            verified.append(run)
            sources.append((path, run, None, None))
            record["completed_ids"].append(run["record"]["run_id"])
        a, b = verified
        require(a["record"]["run_id"] != b["record"]["run_id"],
                "REWRITE-SPREAD: two distinct R0 runs are required")
        require(datetime.fromisoformat(a["record"]["created_at"]) <
                datetime.fromisoformat(b["record"]["created_at"]),
                "REWRITE-SPREAD: the first R0 must precede the second")
        require(a["record"]["commit"] == b["record"]["commit"] and
                a["record"]["gpus"] == b["record"]["gpus"],
                "REWRITE-SPREAD: R0 code and GPU identities must be identical")
        record["runs"].update(r0_first=run_summary(a), r0_second=run_summary(b))
        ids = [row["id"] for row in bundle[1]["rows"]]
        record.update(score.rewrite_spread(ids, a["checks"], b["checks"]))
        for path, adapter, training_build in candidates:
            run = read_score(path, bundle, adapter=adapter,
                             training_build=training_build, repo=repo)
            key = f"{run['record']['procedure']}_epoch_{run['record']['epoch']}"
            require(key not in record["runs"] and
                    run["record"]["run_id"] not in record["completed_ids"],
                    "REWRITE-SPREAD: candidate procedure or run identity repeats")
            record["runs"][key] = run_summary(run)
            record["comparisons"][key] = score.compare_rewrites(
                ids, a["checks"], b["checks"], run["checks"])
            record["completed_ids"].append(run["record"]["run_id"])
            sources.append((path, run, adapter, training_build))
        for path, run, adapter, training_build in sources:
            verify_snapshot(path, run["identity"])
            if adapter is not None:
                verify_adapter(run["record"], adapter, training_build, bundle[0], repo)
        require(score.read_plan(plan_dir, repo=repo)[4:] == bundle[4:],
                "REWRITE-RECORD: frozen plan changed during comparison")
        record["status"] = "completed"
        save()
        (out / "report.md").write_text(markdown(record), encoding="utf-8")
        manifest = mf.build_tree(out, "evaluation", "ste-rewriter-comparison")
        with manifest_path.open("x", encoding="utf-8") as stream:
            stream.write(manifest.to_json())
    except BaseException as exc:
        record["status"] = "failed"
        record["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        save()
        raise
    return record, manifest


def main(argv=None, *, repo=REPO):
    """REWRITE-SPREAD: compare two frozen R0 passes and optional epoch scores."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--r0-first", type=Path, required=True)
    parser.add_argument("--r0-second", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, nargs=3, action="append", default=[],
                        metavar=("RUN", "ADAPTER", "TRAINING_BUILD"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record, manifest = write_comparison(args.plan, args.r0_first, args.r0_second,
            args.out, candidates=args.candidate, repo=repo)
    except (mf.ManifestError, OSError, KeyError, TypeError, IndexError,
            ValueError, RuntimeError) as exc:
        print(f"comparison: stopped: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": record["status"], "tree_hash": manifest.tree_hash,
                      "decode_difference": record["decode_difference"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
