"""GRANITE-R0, GRANITE-SCORES and GRANITE-RECORD: descriptive dense-series reports."""

import argparse
import json
from datetime import datetime
from pathlib import Path

import rewriter_granite as granite
import rewriter_r1_e4_report as measurements
import rewriter_report as report

score, mf, require, e4, profile = granite.score, granite.mf, granite.require, granite.e4, granite.profile


def references(first, second):
    """GRANITE-R0: require two ordered passes from this dense series."""
    a, b = first["record"], second["record"]
    require(a["procedure"] == b["procedure"] == profile.R0 and
            a["r0_pass"] == 1 and b["r0_pass"] == 2 and a["run_id"] != b["run_id"] and
            datetime.fromisoformat(a["created_at"]) < datetime.fromisoformat(b["created_at"]) and
            a["gpus"] == b["gpus"] and a["commit"] == b["commit"],
            "GRANITE-R0: ordered distinct Granite baseline passes are required")


def summary(run):
    """GRANITE-RECORD: include procedure, model, failures and each GPU's memory."""
    return {**report.run_summary(run), "model": profile.MODEL,
            "failures": measurements.extra_failures(run),
            "placement": run["record"]["placement"],
            "verification": run["record"]["verification"]}


def save_report(out, record, name, inputs, repo):
    """GRANITE-RECORD: recheck inputs before sealing a new result artifact."""
    for path, identity in inputs.items():
        _, manifest, digest = score.artifact(path)
        require((manifest.tree_hash, digest) == identity,
                "GRANITE-RECORD: report source artifact changed during verification")
    destination, _ = score.fresh_score_output(out, repo=repo, inputs=inputs)
    destination.mkdir(parents=True)
    (destination / "report.json").write_bytes(e4.json_bytes(record))
    (destination / "report.md").write_text(markdown(record), encoding="utf-8")
    if "loss_curve" in record:
        (destination / "loss.csv").write_text("step,epoch,loss,learning_rate\n" + "".join(
            f"{row['step']},{row['epoch']},{row['loss']},{row['learning_rate']}\n"
            for row in record["loss_curve"]["steps"]), encoding="utf-8")
    require(granite.provenance.clean_commit(repo) == record["commit"],
            "GRANITE-RECORD: report launch commit changed")
    return record, e4.seal(destination, name)


def snapshots(paths):
    """GRANITE-RECORD: capture every input tree identity before making a report."""
    return {path: (manifest.tree_hash, digest) for path in paths
            for _, manifest, digest in [score.artifact(path)]}


def baseline(plan, first_path, second_path, batch, verdicts, out, *, repo=granite.REPO):
    """GRANITE-R0: complete the baseline report and blind review before training."""
    inputs = snapshots((plan, first_path, second_path, batch, verdicts))
    bundle = score.read_plan(plan, repo=repo)
    require(profile.model_for(bundle[0]) == profile.MODEL,
            "GRANITE-SCOPE: Granite baseline requires its own token plan")
    first, second = [report.read_score(path, bundle, repo=repo) for path in (first_path, second_path)]
    references(first, second)
    meaning, meaning_id = measurements.meaning_result(
        verdicts, batch, plan, [(first_path, None, None)], repo)
    ids = [row["id"] for row in bundle[1]["rows"]]
    record = {"schema": "weaver-train/rewriter-granite-r0-report/v1", "status": "completed",
        **profile.IDENTITY, "commit": bundle[0]["commit"], "plan": list(bundle[4:]),
        "runs": {"r0_first": summary(first), "r0_second": summary(second)},
        "spread": score.rewrite_spread(ids, first["checks"], second["checks"]),
        "meaning": meaning_id, "meaning_counts": meaning["by_run"],
        "first_run_id": first["record"]["run_id"]}
    return save_report(out, record, "ste-rewriter-granite-r0-report", inputs, repo)


def read_baseline(directory, bundle):
    """GRANITE-R0: training requires the completed report for this frozen plan."""
    root, manifest, digest = score.artifact(directory)
    record = json.loads(score.captured_file(root, manifest, "report.json"))
    require(manifest.kind == "evaluation" and manifest.name == "ste-rewriter-granite-r0-report" and
            record["schema"] == "weaver-train/rewriter-granite-r0-report/v1" and
            record["status"] == "completed" and "failure" not in record,
            "GRANITE-R0: a completed baseline report is required before training")
    require(profile.model_for(record) == profile.MODEL and record["plan"] == list(bundle[4:]) and
            record["commit"] == bundle[0]["commit"] and
            record["first_run_id"] in record["meaning_counts"],
            "GRANITE-R0: baseline plan, launch or meaning review differs")
    return record, {"tree_hash": manifest.tree_hash, "manifest_sha256": digest}


def write_report(args, *, repo=granite.REPO):
    """GRANITE-SCORES: compare four epochs only with Granite's own first R0."""
    paths = [args.experiment, args.build, args.train, args.baseline, args.r0_first,
             args.r0_second, args.review_batch, args.review_result, *args.heldout, *args.training]
    inputs = snapshots(paths)
    experiment, experiment_id = granite.read_experiment(args.experiment, repo=repo)
    held_plan = score.read_plan(args.experiment / "heldout", repo=repo)
    train_plan = score.read_plan(args.experiment / "training", repo=repo)
    base, base_id = read_baseline(args.baseline, held_plan)
    first, second = [report.read_score(path, held_plan, repo=repo)
                     for path in (args.r0_first, args.r0_second)]
    references(first, second)
    require(base["runs"]["r0_first"]["identity"] == first["identity"] and
            base["runs"]["r0_second"]["identity"] == second["identity"],
            "GRANITE-R0: final references differ from the completed baseline")
    producer = mf.load_run(args.train / "train/run.json")
    granite.producer_proof(args.train / "train", producer, held_plan[0], repo)
    train_record = json.loads((args.train / "procedure.json").read_bytes())
    require(train_record["baseline"] == base_id,
            "GRANITE-R0: training used a different baseline report")
    curve = measurements.loss_curve(producer, json.loads(
        (args.train / "train/trainer/log_history.json").read_bytes()))
    sources, heldout, training = [], [], []
    require(len(args.heldout) == len(args.training) == 4,
            "GRANITE-SCORES: four held-out and four training scores are required")
    for epoch, held, diagnostic in zip(e4.EPOCHS, args.heldout, args.training):
        adapter = args.train / f"train/adapter-epoch-{epoch}"
        sources.append((held, adapter, args.build))
        for path, bundle, destination in ((held, held_plan, heldout), (diagnostic, train_plan, training)):
            run = report.read_score(path, bundle, adapter=adapter, training_build=args.build, repo=repo)
            require(run["record"]["procedure"] == profile.R1 and run["record"]["epoch"] == epoch,
                    "GRANITE-SCORES: score sequence does not identify each epoch")
            destination.append(run)
    meaning, meaning_id = measurements.meaning_result(
        args.review_result, args.review_batch, args.experiment / "heldout", sources, repo)
    ids = [row["id"] for row in held_plan[1]["rows"]]
    epochs = [{"epoch": epoch, "heldout": summary(held), "training": summary(diagnostic),
        "gap": measurements.score_gap(held["summary"], diagnostic["summary"]),
        "paired": score.compare_rewrites(ids, first["checks"], second["checks"], held["checks"]),
        "meaning": meaning["by_run"][held["record"]["run_id"]]}
        for epoch, held, diagnostic in zip(e4.EPOCHS, heldout, training)]
    record = {"schema": "weaver-train/rewriter-granite-r1-report/v1", "status": "completed",
        **profile.IDENTITY, "commit": experiment["commit"], "experiment": experiment_id,
        "baseline": base_id, "references": base["runs"], "epochs": epochs, "loss_curve": curve,
        "training": train_record, "meaning": meaning_id, "trainable_parameters": 140509184}
    return save_report(args.out, record, "ste-rewriter-granite-r1-report", inputs, repo)


def markdown(record):
    """GRANITE-RECORD: public figures contain neither text samples nor local paths."""
    lines = ["# Granite rewriter measurements", "", "Engine: python-hf. Precision: bf16.", "",
             "Model: ibm-granite/granite-4.2-30b.", "",
             "Each comparison uses Granite's first R0 and its own R0 decode margin.", ""]
    runs = list(record.get("runs", {}).items())
    for epoch in record.get("epochs", []):
        runs.extend((f"epoch_{epoch['epoch']}_{kind}", epoch[kind]) for kind in ("heldout", "training"))
    lines += ["| Procedure | Passed | Total | Wall seconds |", "| --- | ---: | ---: | ---: |"]
    for label, run in runs:
        checks = run["checks"]
        lines.append(f"| {label} | {checks['passed']} | {checks['denominator']} | {run['wall_seconds']:.6f} |")
    lines += ["", "| Procedure | GPU | Peak allocated bytes | Peak reserved bytes |",
              "| --- | ---: | ---: | ---: |"]
    for label, run in runs:
        for device, memory in run["peak_memory_bytes"].items():
            lines.append(f"| {label} | {device} | {memory['allocated']} | {memory['reserved']} |")
    lines += ["", "| Procedure | Failure | Count |", "| --- | --- | ---: |"]
    for label, run in runs:
        for key, count in {**run["checks"]["failure_rules"], **run["failures"]}.items():
            lines.append(f"| {label} | {key} | {count} |")
    if "epochs" in record:
        lines += ["", "| Epoch | Training minus held-out, points | Mean loss | Steps | Reviewed |",
                  "| ---: | ---: | ---: | ---: | ---: |"]
        for epoch, loss in zip(record["epochs"], record["loss_curve"]["epochs"]):
            lines.append(f"| {epoch['epoch']} | {epoch['gap']['gap_percentage_points']} | "
                f"{loss['mean_loss']:.9f} | {loss['steps']} | {epoch['meaning']['reviewed']} |")
        lines += ["", "Training rates describe the fixed training sample, not an independent evaluation.",
                  "The cosine schedule decays to zero. A flat final loss does not identify a cause."]
    lines += ["", "The JSON record retains kind and source-bin counts, paired intervals and meaning verdicts.",
              "The separate loss CSV retains every optimizer-step loss and learning rate.", "",
              f"Commit: {record['commit']['rev']}", ""]
    return "\n".join(lines)


def main(argv=None):
    """GRANITE-FILES: build baseline or final records from retained outputs only."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    baseline_parser = commands.add_parser("baseline")
    for name in ("plan", "r0-first", "r0-second", "review-batch", "review-result", "out"):
        baseline_parser.add_argument("--" + name, type=Path, required=True)
    final = commands.add_parser("final")
    for name in ("experiment", "build", "train", "baseline", "r0-first", "r0-second",
                 "review-batch", "review-result", "out"):
        final.add_argument("--" + name, type=Path, required=True)
    for name in ("heldout", "training"):
        final.add_argument("--" + name, type=Path, nargs=4, required=True)
    args = parser.parse_args(argv)
    if args.command == "baseline":
        baseline(args.plan, args.r0_first, args.r0_second, args.review_batch, args.review_result, args.out)
    else:
        write_report(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
