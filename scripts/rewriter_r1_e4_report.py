"""REWRITE-R1-E4-REPORT and REWRITE-R1-E4-EXPERTS: descriptive measurements only."""

from __future__ import annotations

import inspect
import json
import math
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import rewriter_r1_e4 as e4
import rewriter_report as report

score, require, mf = e4.score, e4.require, e4.mf


def step_rows(rows, field, expected):
    """REWRITE-R1-E4-REPORT: join complete finite measurements by optimizer step."""
    values = {}
    for row in rows:
        step = row.get("step")
        require(type(step) is int and step in expected,
                f"REWRITE-R1-E4-REPORT: invalid {field} step identity")
        require(step not in values, f"REWRITE-R1-E4-REPORT: duplicate {field} step")
        value = row.get(field)
        require(type(value) in (int, float) and math.isfinite(value),
                f"REWRITE-R1-E4-REPORT: absent or non-finite {field}")
        values[step] = row
    require(set(values) == expected, f"REWRITE-R1-E4-REPORT: missing {field} steps")
    return values


def loss_curve(producer, logs):
    """REWRITE-R1-E4-REPORT: use recorded rates and arithmetic means of logged losses."""
    require(producer.results.get("global_step") == 100,
            "REWRITE-R1-E4-REPORT: expected 100 optimizer steps")
    expected = set(range(1, 101))
    losses = step_rows(producer.results["loss_history"], "loss", expected)
    logged = [row for row in logs if "loss" in row or "learning_rate" in row]
    rates = step_rows(logged, "learning_rate", expected)
    curve = []
    for step in sorted(expected):
        loss, rate = losses[step], rates[step]
        epoch = loss.get("epoch")
        require(type(epoch) in (int, float) and math.isfinite(epoch) and
                abs(epoch - step / 25) < 1e-9 and rate.get("epoch") == epoch,
                "REWRITE-R1-E4-REPORT: step epoch identity differs")
        require(rate.get("loss") == loss["loss"],
                "REWRITE-R1-E4-REPORT: loss records disagree by step")
        curve.append({"step": step, "epoch": epoch, "loss": loss["loss"],
                      "learning_rate": rate["learning_rate"]})
    epochs = []
    for epoch in e4.EPOCHS:
        values = [row["loss"] for row in curve if math.ceil(row["epoch"]) == epoch]
        epochs.append({"epoch": epoch, "steps": len(values),
                       "mean_loss": math.fsum(values) / len(values)})
    return {"steps": curve, "epochs": epochs}


def paired_scores(ids, r0_first, r0_second, original_r1, current):
    """REWRITE-R1-E4-SCORES: use the original decode difference for both references."""
    spread = score.rewrite_spread(ids, r0_first, r0_second)
    values = score.rewrite_outcomes(ids, current)
    return {"decode_spread": spread, "r0_first": score.compare_pairs(
                score.rewrite_outcomes(ids, r0_first), values, spread["decode_difference"]),
            "r1_epoch_2": score.compare_pairs(
                score.rewrite_outcomes(ids, original_r1), values, spread["decode_difference"])}


def score_gap(heldout, training):
    """REWRITE-R1-E4-TRAIN-SCORES: report the training minus held-out percentage points."""
    require(heldout["denominator"] == 100 and training["denominator"] == 50,
            "REWRITE-R1-E4-TRAIN-SCORES: score gap denominators differ")
    return {"heldout_passed": heldout["passed"], "heldout_denominator": 100,
            "heldout_rate": heldout["passed"] / 100,
            "training_passed": training["passed"], "training_denominator": 50,
            "training_rate": training["passed"] / 50,
            "gap_percentage_points": 2 * training["passed"] - heldout["passed"]}


def expert_modules(model, current_targets):
    """REWRITE-R1-E4-EXPERTS: count unique shared-expert linear matrices at rank 16."""
    from torch import nn

    layers, seen = [], set()
    for index, layer in enumerate(model.model.layers):
        mixer = layer.mixer
        if not hasattr(mixer, "shared_experts"):
            continue
        modules = []
        for relative, module in mixer.shared_experts.named_modules():
            if not isinstance(module, nn.Linear):
                continue
            name = f"model.layers.{index}.mixer.shared_experts.{relative}"
            duplicate = id(module.weight) in seen
            existing = any(name == target or name.endswith("." + target)
                           for target in current_targets)
            seen.add(id(module.weight))
            modules.append({"name": name, "in_features": module.in_features,
                "out_features": module.out_features,
                "weight_shape": list(module.weight.shape), "rank": 16,
                "already_targeted": existing, "duplicate_weight": duplicate,
                "added_parameters": 0 if existing or duplicate else
                    16 * (module.in_features + module.out_features)})
        require(bool(modules), "REWRITE-R1-E4-EXPERTS: shared expert has no linear modules")
        layers.append({"layer": index, "modules": modules,
                       "added_parameters": sum(item["added_parameters"] for item in modules)})
    require(bool(layers), "REWRITE-R1-E4-EXPERTS: no shared experts were found")
    return {"rank": 16, "layers": layers,
            "added_parameters": sum(layer["added_parameters"] for layer in layers)}


def inventory(out_dir, *, repo=e4.REPO):
    """REWRITE-R1-E4-EXPERTS: inspect only a meta model and verify the full reference twice."""
    import torch
    from transformers import NemotronHConfig, NemotronHForCausalLM

    started = time.monotonic()
    imports = e4.provenance.capture(repo)
    config, config_sha = e4.configuration(repo)
    manifest_path = score.loader.manifest_path_for(e4.build.MODEL)
    manifest = mf.load_tree(manifest_path)
    model_dir = score.loader.resolve_dir(manifest)
    mf.require_tree(manifest, model_dir)
    model_config = NemotronHConfig.from_pretrained(model_dir, local_files_only=True)
    with torch.device("meta"):
        model = NemotronHForCausalLM(model_config)
    result = expert_modules(model, config["lora"]["target_modules"])
    implementations = {}
    for cls in {type(module) for module in model.modules()}:
        module = sys.modules[cls.__module__]
        path = Path(inspect.getfile(module))
        implementations[cls.__module__] = mf.sha256_file(path)
    result.update(schema="weaver-train/rewriter-r1-e4-experts/v1",
                  commit=imports["commit"], model=e4.build.MODEL,
                  model_tree_hash=manifest.tree_hash,
                  model_manifest_sha256=mf.sha256_file(manifest_path),
                  model_config_sha256=mf.sha256_file(model_dir / "config.json"),
                  training_config_sha256=config_sha, implementation_sha256=implementations,
                  device="meta", imports_before=imports)
    mf.require_tree(manifest, model_dir)
    result["imports_after"] = e4.provenance.capture(repo, previous=imports)
    result["wall_seconds"] = time.monotonic() - started
    out, _ = score.fresh_score_output(out_dir, repo=repo, inputs=(model_dir,))
    out.mkdir(parents=True)
    (out / "inventory.json").write_bytes(e4.json_bytes(result))
    return result, e4.seal(out, "ste-rewriter-r1-e4-experts")


def meaning_result(directory, batch, plan, sources, repo):
    """REWRITE-R1-E4-REVIEW: replay ingestion against retained grader bytes and hashes."""
    import rewriter_review as review

    root, manifest, manifest_sha = score.artifact(directory)
    require(manifest.kind == "evaluation" and manifest.name == "ste-rewriter-review-verdicts",
            "REWRITE-R1-E4-REVIEW: a completed verdict artifact is required")
    record = json.loads(score.captured_file(root, manifest, "review.json"))
    require(record["status"] == "completed" and "failure" not in record,
            "REWRITE-R1-E4-REVIEW: meaning review has not completed")
    audit, _ = review.read_batch(batch, plan, sources, repo)
    with tempfile.TemporaryDirectory(prefix="weaver-r1-e4-verdict-check-") as scratch:
        expected, _ = review.ingest(batch, plan, sources, root / "original-verdicts.jsonl",
            Path(scratch) / "verified", input_sha256=audit["input_sha256"],
            prompt_sha256=audit["prompt_sha256"],
            verdicts_sha256=record["deposited_verdicts_sha256"],
            overrides=root / "overrides.jsonl", repo=repo)
        require(all(record[key] == expected[key] for key in (
            "schema", "model", "batch_id", "batch", "summary", "by_run", "unreviewed",
            "completed_ids", "pending_review_ids", "overrides_sha256")) and
            score.captured_file(root, manifest, "verdicts.jsonl") ==
            (Path(scratch) / "verified/verdicts.jsonl").read_bytes(),
            "REWRITE-R1-E4-REVIEW: retained verdict counts or identities differ")
    return record, {"tree_hash": manifest.tree_hash, "manifest_sha256": manifest_sha}


def extra_failures(run):
    """REWRITE-R1-E4-SCORES: preserve failure categories beyond the frozen rule totals."""
    checks = run["checks"]
    return {"missing": sum(row["output"] is None for row in checks),
            "empty": sum(row["output"] == "" for row in checks),
            "token_limit": sum(row["raw"] is not None and
                               row["raw"]["finish_reason"] == "length" for row in checks),
            "placeholders": run["summary"]["failure_rules"].get("placeholders", 0)}


def write_report(args, *, repo=e4.REPO):
    """REWRITE-R1-E4-REPORT: verify all source records before a new final report."""
    paths = [args.experiment, args.build, args.train, args.r0_first, args.r0_second,
             args.r1_score, args.r1_adapter, args.r1_build, args.review_batch,
             args.review_result, args.inventory, *args.heldout, *args.training]
    snapshots = {path: (manifest.tree_hash, digest) for path in paths
                 for _, manifest, digest in [score.artifact(path)]}
    experiment, experiment_id = e4.read_experiment(args.experiment, repo=repo)
    held_plan = score.read_plan(args.experiment / "heldout", repo=repo)
    train_plan = score.read_plan(args.experiment / "training", repo=repo)
    reference = score.read_plan(args.experiment / "reference", repo=repo)
    _, old_build_id = e4.training_build(args.r1_build)
    require(old_build_id == experiment["original_build"],
            "REWRITE-R1-E4-SCORES: original R1 build differs from the frozen reference")
    first = report.read_score(args.r0_first, reference, repo=repo)
    second = report.read_score(args.r0_second, reference, repo=repo)
    original = report.read_score(args.r1_score, reference, adapter=args.r1_adapter,
                                  training_build=args.r1_build, repo=repo)
    require(first["record"]["procedure"] == second["record"]["procedure"] == "r0" and
            first["record"]["run_id"] != second["record"]["run_id"] and
            datetime.fromisoformat(first["record"]["created_at"]) <
            datetime.fromisoformat(second["record"]["created_at"]) and
            first["record"]["gpus"] == second["record"]["gpus"],
            "REWRITE-R1-E4-SCORES: ordered distinct R0 references are required")
    require(original["record"]["procedure"] == "r1" and original["record"]["epoch"] == 2,
            "REWRITE-R1-E4-SCORES: original R1 epoch 2 is required")
    root, train_manifest, train_manifest_sha = score.artifact(args.train)
    train_record = json.loads(score.captured_file(root, train_manifest, "procedure.json"))
    train_dir = root / "train"
    producer = mf.RunRecord(**json.loads((train_dir / "run.json").read_bytes()))
    proof = e4.producer_proof(train_dir, producer, held_plan[0], repo)
    require(proof["experiment"] == experiment_id,
            "REWRITE-R1-E4-REPORT: training uses a different frozen experiment")
    curve = loss_curve(producer, json.loads((train_dir / "trainer/log_history.json").read_bytes()))
    require(train_record["loss_curve"] == curve,
            "REWRITE-R1-E4-REPORT: retained training curve differs")
    sources, heldout, training = [], [], []
    for epoch, held_path, train_path in zip(e4.EPOCHS, args.heldout, args.training, strict=True):
        adapter = train_dir / f"adapter-epoch-{epoch}"
        sources.append((held_path, adapter, args.build))
        for path, plan, destination in ((held_path, held_plan, heldout),
                                        (train_path, train_plan, training)):
            run = report.read_score(path, plan, adapter=adapter,
                                    training_build=args.build, repo=repo)
            require(run["record"]["procedure"] == "r1-e4" and run["record"]["epoch"] == epoch,
                    "REWRITE-R1-E4-REPORT: score epoch or procedure differs")
            destination.append(run)
    meaning, meaning_id = meaning_result(args.review_result, args.review_batch,
                                         args.experiment / "heldout", sources, repo)
    inventory_root, inventory_manifest, inventory_sha = score.artifact(args.inventory)
    require(inventory_manifest.name == "ste-rewriter-r1-e4-experts",
            "REWRITE-R1-E4-EXPERTS: shared expert inventory artifact is required")
    experts = json.loads(score.captured_file(inventory_root, inventory_manifest, "inventory.json"))
    require(experts["model_tree_hash"] == held_plan[0]["tree_hash"] and
            experts["commit"] == experiment["commit"] and
            experts["training_config_sha256"] == experiment["config_sha256"],
            "REWRITE-R1-E4-EXPERTS: inventory reference identities differ")
    e4.provenance.verify_pair(experts, repo, experiment["commit"])
    ids = [row["id"] for row in held_plan[1]["rows"]]
    epochs = []
    for number, held, training_run in zip(e4.EPOCHS, heldout, training):
        epochs.append({"epoch": number, "heldout": report.run_summary(held),
            "training": report.run_summary(training_run),
            "gap": score_gap(held["summary"], training_run["summary"]),
            "heldout_failures": extra_failures(held),
            "training_failures": extra_failures(training_run),
            "paired": paired_scores(ids, first["checks"], second["checks"],
                                      original["checks"], held["checks"]),
            "meaning": meaning["by_run"][held["record"]["run_id"]]})
    record = {"schema": "weaver-train/rewriter-r1-e4-report/v1", "status": "completed",
        "commit": experiment["commit"], "engine": "python-hf", "precision": "bf16",
        "experiment": experiment_id, "training": train_record,
        "training_manifest_sha256": train_manifest_sha, "loss_curve": curve,
        "references": {"r0_first": report.run_summary(first),
                       "r0_second": report.run_summary(second),
                       "r1_epoch_2": report.run_summary(original)}, "epochs": epochs,
        "meaning": meaning_id, "inventory_manifest_sha256": inventory_sha, "experts": experts}
    out, _ = score.fresh_score_output(args.out, repo=repo, inputs=paths)
    # REWRITE-RECORD: every source must still match its retained manifest at completion.
    for path in paths:
        _, manifest, digest = score.artifact(path)
        require((manifest.tree_hash, digest) == snapshots[path],
                "REWRITE-R1-E4-REPORT: source artifact changed during verification")
    require(e4.provenance.clean_commit(repo) == experiment["commit"],
            "REWRITE-R1-E4-REPORT: checkout changed during report verification")
    out.mkdir(parents=True)
    (out / "report.json").write_bytes(e4.json_bytes(record))
    (out / "report.md").write_text(markdown(record), encoding="utf-8")
    (out / "loss.csv").write_text("step,epoch,loss,learning_rate\n" + "".join(
        f"{row['step']},{row['epoch']},{row['loss']},{row['learning_rate']}\n"
        for row in curve["steps"]), encoding="utf-8")
    return record, e4.seal(out, "ste-rewriter-r1-e4-report")


def markdown(record):
    """REWRITE-R1-E4-REPORT: produce a public note containing numbers and hashes only."""
    lines = ["# R1-E4 measurements", "", "Engine: python-hf. Precision: bf16.", "",
        "The training sample is a diagnostic on training pairs. It is not an independent evaluation.",
        "The cosine schedule decreases to zero. A flat final loss does not identify a cause.", "",
        "| Epoch | Held-out | Training sample | Gap, points | Mean loss | Steps | Reviewed |",
        "| --- | --- | --- | --- | --- | --- | --- |"]
    for epoch, losses in zip(record["epochs"], record["loss_curve"]["epochs"]):
        gap = epoch["gap"]
        lines.append(f"| {epoch['epoch']} | {gap['heldout_passed']}/100 | "
            f"{gap['training_passed']}/50 | {gap['gap_percentage_points']} | "
            f"{losses['mean_loss']:.9g} | {losses['steps']} | {epoch['meaning']['reviewed']} |")
    lines += ["", "| Epoch | Kind or source bin | Passed | Total |",
              "| --- | --- | --- | --- |"]
    for epoch in record["epochs"]:
        for field in ("by_kind", "by_bin"):
            for label, value in epoch["heldout"]["checks"][field].items():
                lines.append(f"| {epoch['epoch']} | {field}:{label} | {value['passed']} | {value['total']} |")
    lines += ["", "| Epoch | Sample | Failure rules | Output counts |", "| --- | --- | --- | --- |"]
    for epoch in record["epochs"]:
        for sample in ("heldout", "training"):
            rules = epoch[sample]["checks"]["failure_rules"]
            counts = epoch[sample + "_failures"]
            lines.append(f"| {epoch['epoch']} | {sample} | {json.dumps(rules, sort_keys=True)} | "
                         f"{json.dumps(counts, sort_keys=True)} |")
    lines += ["", "| Epoch | Reference | Gain | Loss | Difference | 95% interval | p | Decode margin | Result |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for epoch in record["epochs"]:
        for reference in ("r0_first", "r1_epoch_2"):
            value = epoch["paired"][reference]
            lines.append(f"| {epoch['epoch']} | {reference} | {value['b']} | {value['c']} | "
                f"{value['difference']:.9g} | {value['interval_95']} | {value['p']:.9g} | "
                f"{value['decode_difference']:.9g} | {value['result']} |")
    lines += ["", "| Epoch | Verdict counts | Reviewed | Checker accepted | Defects | Defects per 100 | Affected rate |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for epoch in record["epochs"]:
        value = epoch["meaning"]
        lines.append(f"| {epoch['epoch']} | {json.dumps(value['verdicts'])} | {value['reviewed']} | "
            f"{value['reviewed_checker_accepted']} | {value['distinct_defects']} | "
            f"{value['defects_per_100']} | {value['segments_with_defects_rate']} |")
    lines += ["", "| Procedure | Seconds | GPU | Peak allocated bytes | Peak reserved bytes |",
              "| --- | --- | --- | --- | --- |"]
    procedures = [("training", record["training"])]
    procedures += [(f"{sample}-epoch-{epoch['epoch']}", epoch[sample])
                    for epoch in record["epochs"] for sample in ("heldout", "training")]
    for name, procedure in procedures:
        for gpu, peak in procedure["peak_memory_bytes"].items():
            lines.append(f"| {name} | {procedure['wall_seconds']:.9f} | {gpu} | "
                         f"{peak['allocated']} | {peak['reserved']} |")
    lines += ["", "| Step | Epoch | Loss | Recorded learning rate |", "| --- | --- | --- | --- |"]
    for row in record["loss_curve"]["steps"]:
        lines.append(f"| {row['step']} | {row['epoch']} | {row['loss']} | {row['learning_rate']} |")
    lines += ["", "| Shared expert module | Weight dimensions | Added rank-16 parameters |",
              "| --- | --- | --- |"]
    for layer in record["experts"]["layers"]:
        for module in layer["modules"]:
            lines.append(f"| {module['name']} | {module['weight_shape']} | {module['added_parameters']} |")
        lines.append(f"| Layer {layer['layer']} total | | {layer['added_parameters']} |")
    lines += [f"| Model total | | {record['experts']['added_parameters']} |", "",
              f"Experiment tree: {record['experiment']['tree_hash']}",
              f"Training manifest: {record['training_manifest_sha256']}",
              f"Meaning manifest: {record['meaning']['manifest_sha256']}",
              f"Expert inventory manifest: {record['inventory_manifest_sha256']}", "",
              "Original R1 epoch 2 producer SHA256: " +
              record["references"]["r1_epoch_2"]["adapter"]["producer"]["run_sha256"],
              "The original R1 provenance correction remains recorded in "
              "[PR 38](https://github.com/toddwbucy/WeaverTrain/pull/38).", "",
              f"Commit: {record['commit']['rev']}", ""]
    return "\n".join(lines)
