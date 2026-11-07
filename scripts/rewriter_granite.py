"""GRANITE-FILES: freeze, verify and train the separate dense rewriter series."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import rewriter_granite_profile as profile
import rewriter_provenance as provenance
import rewriter_r1_e4 as e4
import rewriter_score as score

build, mf, require, REPO = score.build, score.mf, score.require, score.REPO
CONFIG = "config/rewriter-granite.toml"
EPOCHS = (1, 2, 3, 4)
TRAINING_SAMPLE = "e186c3bdbd87bff4a2a2585301e7e8277526226b95282f9d4536e94cdfaa1fd9"


def configuration(repo):
    """GRANITE-R1: the only recipe difference from Nemotron R1-E4 is the target set."""
    expected = copy.deepcopy(e4.configuration(repo)[0])
    expected["lora"]["target_modules"] = profile.TARGETS
    raw = (repo / CONFIG).read_bytes()
    current = tomllib.loads(raw.decode())
    require(current == expected, "GRANITE-R1: configuration differs from the approved recipe")
    return current, build.digest(raw)


def loaded_placement(model):
    """GRANITE-MODEL, GRANITE-VERIFY: retain the actual native path and placement."""
    from weaver_train import trainer

    base = model.get_base_model() if hasattr(model, "get_base_model") else model
    require(type(base).__name__ == "GraniteForCausalLM" and
            base.config.model_type == "granite",
            "GRANITE-MODEL: loaded implementation is not native dense Granite")
    result = trainer.granite_placement(model)
    require(set(result["parameter_devices"].values()) == {"cuda:0", "cuda:1"},
            "GRANITE-VERIFY: weights must span exactly the two selected GPUs")
    return result


def verify_environment(out, repo):
    """GRANITE-VERIFY: retain one fresh verification, with no repeat on failure."""
    commit = provenance.clean_commit(repo)
    require(os.environ.get("CUDA_DEVICE_ORDER") == "PCI_BUS_ID" and
            os.environ.get("CUDA_VISIBLE_DEVICES") == "0,1",
            "GRANITE-VERIFY: load the approved GPU selection from .env")
    started = time.monotonic()
    record = {"commit": commit, "started_at": datetime.now(timezone.utc).isoformat(),
              "status": "started", "command": "scripts/setup-env.sh --only verify --reverify"}
    path = out / "verification.json"
    path.write_bytes(e4.json_bytes(record))
    with (out / "verification.log").open("xb") as stream:
        result = subprocess.run(["bash", "-c", "set -a\n. ./.env\nset +a\n"
            "scripts/setup-env.sh --only verify --reverify"], cwd=repo,
            stdout=stream, stderr=subprocess.STDOUT)
    record.update(finished_at=datetime.now(timezone.utc).isoformat(),
                  wall_seconds=time.monotonic() - started, exit_code=result.returncode,
                  status="completed" if result.returncode == 0 else "failed",
                  log_sha256=mf.sha256_file(out / "verification.log"))
    path.write_bytes(e4.json_bytes(record))
    require(result.returncode == 0, "GRANITE-VERIFY: fresh environment verification failed")
    require(provenance.clean_commit(repo) == commit,
            "GRANITE-VERIFY: checkout changed during environment verification")
    return record


def verify_build(current, original, commit, repo):
    """GRANITE-DATA: compare source identities without reusing Nemotron token pins."""
    require(profile.model_for(current) == profile.MODEL and current["procedure"] == profile.R1,
            "GRANITE-DATA: a Granite training build is required")
    require(profile.model_for(original) == build.MODEL and
            original["procedure"] in ("r1", "r1-e4"),
            "GRANITE-DATA: the reference must be the approved Nemotron pair build")
    require(current["commit"] == commit, "GRANITE-RECORD: training build launch differs")
    e4.approved_ids(current["selection"], repo)
    for field in ("selection", "pair_manifest", "pair_manifest_sha256", "source_dataset",
                  "source_revision", "folded_prompt", "prompt_provenance", "limits"):
        require(current[field] == original[field], f"GRANITE-DATA: source {field} differs")
    require(current["template_hash"] == profile.TEMPLATE and current["tools"] is False and
            current["enable_thinking"] is False and current["max_seq_length"] == 49152,
            "GRANITE-DATA: training render fields differ")
    require(current["counts"]["pairs"] == 200 and current["tokens_per_epoch"] ==
            [dict(epoch=epoch, **current["counts"]) for epoch in EPOCHS],
            "GRANITE-DATA: four complete training token records are required")
    provenance.verify_pair(current, repo, commit)


def probe_record(directory, commit, model_tree, repo):
    """GRANITE-MEMORY: require the exact successful probe before R0 or training."""
    root, manifest, digest = score.artifact(directory)
    record = json.loads(score.captured_file(root, manifest, "procedure.json"))
    require(manifest.kind == "evaluation" and manifest.name == "ste-rewriter-granite-probe",
            "GRANITE-MEMORY: expected the separate memory probe artifact")
    require(record["status"] == "completed" and "failure" not in record and
            record["commit"] == commit and record["tree_hash"] == model_tree and
            profile.model_for(record) == profile.MODEL,
            "GRANITE-MEMORY: probe model, launch or status differs")
    require(record["config_sha256"] == configuration(repo)[1] and
            record["sequence_length"] == 49152 and record["finite_gradients"] is True and
            record["trainable_parameters"] == 140509184 and len(record["target_inventory"]) == 448,
            "GRANITE-MEMORY: exact context, targets and training path are required")
    padding = record.get("padding_token_ids", {})
    profile.verify_padding_ids(padding.get("before"), loaded=False)
    profile.verify_padding_ids(padding.get("after"), loaded=True)
    provenance.verify_pair(record, repo, commit)
    return record, {"tree_hash": manifest.tree_hash, "manifest_sha256": digest}


def freeze(build_dir, reference_build, memory, folded, out_dir, *, repo=REPO):
    """GRANITE-DATA, GRANITE-SCORES: freeze new tokens for the same source samples."""
    imports = provenance.capture(repo)
    commit = imports["commit"]
    current, current_id = e4.training_build(build_dir)
    original, original_id = e4.training_build(reference_build)
    verify_build(current, original, commit, repo)
    _, memory_id = probe_record(memory, commit, current["tree_hash"], repo)
    _, config_sha = configuration(repo)
    out, _ = score.fresh_score_output(out_dir, repo=repo,
                                      inputs=(build_dir, reference_build, memory))
    out.mkdir(parents=True)
    score.freeze_plan(build_dir, folded, out / "heldout", repo=repo)
    bundle = score.read_plan(out / "heldout", repo=repo)
    kit = e4.training_kit(current["selection"], repo)
    require(kit["ordered_ids_sha256"] == TRAINING_SAMPLE,
            "GRANITE-SCORES: training sample differs from the fixed Nemotron sequence")
    evidence = build.Evidence(repo)
    tokenizer, model_manifest, model_dir, _ = build.build_tokenizer(evidence, profile.MODEL)
    entries = score.preflight(tokenizer, kit, bundle[3], repo=repo, expected_count=50,
                              model=profile.MODEL)
    plan = {**bundle[0], "schema": "weaver-train/rewriter-training-plan/v1",
            "denominator": 50, "source_seed": 2034, "review_sample": [],
            "input_sha256": kit["input_sha256"], "training_selection": current["selection"],
            "selected_ids": kit["selected_ids"], "ordered_ids_sha256": kit["ordered_ids_sha256"]}
    training = out / "training"
    training.mkdir()
    inputs = [{key: row[key] for key in ("id", "kind", "text")} for row in kit["rows"]]
    for name, raw in {"plan.json": e4.json_bytes(plan), "kit.json": e4.json_bytes(kit),
        "entries.jsonl": score.jsonl_bytes(entries), "input.jsonl": score.jsonl_bytes(inputs),
        "folded.txt": bundle[3]}.items():
        (training / name).write_bytes(raw)
    e4.seal(training, "ste-rewriter-training-plan")
    record = {"schema": "weaver-train/rewriter-granite-plan/v1", **profile.IDENTITY,
        "commit": commit, "config_sha256": config_sha, "training_build": current_id,
        "reference_build": original_id, "reference_record": original, "current_record": current,
        "memory_directory": str(Path(memory).resolve()), "memory": memory_id,
        "imports_before": imports}
    evidence.verify()
    mf.require_tree(model_manifest, model_dir)
    record["imports_after"] = provenance.capture(repo, previous=imports)
    (out / "experiment.json").write_bytes(e4.json_bytes(record))
    manifest = e4.seal(out, "ste-rewriter-granite-plan")
    read_experiment(out, repo=repo)
    return record, manifest


def read_experiment(directory, *, repo=REPO):
    """GRANITE-RECORD: bind both plans, the probe and the new build to one launch."""
    root, manifest, digest = score.artifact(directory)
    require(manifest.name == "ste-rewriter-granite-plan" and manifest.kind == "evaluation",
            "GRANITE-SCOPE: expected the separate Granite experiment")
    record = json.loads(score.captured_file(root, manifest, "experiment.json"))
    require(record["schema"] == "weaver-train/rewriter-granite-plan/v1" and
            profile.model_for(record) == profile.MODEL and
            record["commit"] == provenance.clean_commit(repo),
            "GRANITE-RECORD: experiment launch or series differs")
    require(record["config_sha256"] == configuration(repo)[1],
            "GRANITE-R1: frozen configuration differs")
    verify_build(record["current_record"], record["reference_record"], record["commit"], repo)
    provenance.verify_pair(record, repo, record["commit"])
    require(probe_record(record["memory_directory"], record["commit"],
                         record["current_record"]["tree_hash"], repo)[1] == record["memory"],
            "GRANITE-MEMORY: frozen probe identity differs")
    heldout = score.read_plan(root / "heldout", repo=repo)
    training = score.read_plan(root / "training", repo=repo)
    for bundle in (heldout, training):
        plan = bundle[0]
        require(profile.model_for(plan) == profile.MODEL and plan["commit"] == record["commit"] and
                plan["build_sha256"] == record["training_build"]["build_sha256"] and
                plan["build_tree_hash"] == record["training_build"]["tree_hash"] and
                plan["build_manifest_sha256"] == record["training_build"]["manifest_sha256"],
                "GRANITE-RECORD: plan build or model identity differs")
        score.require_fields(plan, record["current_record"])
    require(training[0]["ordered_ids_sha256"] == TRAINING_SAMPLE and
            training[0]["review_sample"] == [] and training[3] == heldout[3],
            "GRANITE-SCORES: training sample, prompt or review selection differs")
    for key in (*score.FIELDS, "inputs", "environment_sha256", "folded_prompt",
                "prompt_provenance", "sampler", "reviewer_model", "reviewer_prompt_sha256"):
        require(training[0][key] == heldout[0][key],
                f"GRANITE-SCORES: training and held-out {key} differ")
    return record, {"tree_hash": manifest.tree_hash, "manifest_sha256": digest}


def score_plan(plan_dir, bundle, repo):
    """GRANITE-SCORES: each score must use a plan from the frozen experiment."""
    _, identity = read_experiment(Path(plan_dir).parent, repo=repo)
    require(Path(plan_dir).name in ("heldout", "training") and
            score.read_plan(plan_dir, repo=repo)[4:] == bundle[4:],
            "GRANITE-SCORES: score plan is not a frozen experiment member")
    return identity


def verify_score_proof(proof, bundle, experiment):
    """GRANITE-RECORD: bind an adapter score to its producer and frozen plan."""
    require(proof["experiment"] == experiment and list(bundle[4:]) in proof["score_plans"].values(),
            "GRANITE-RECORD: adapter producer used different experiment plans")


def producer_proof(train_dir, producer, plan, repo):
    """GRANITE-RECORD: reject an absent, changed, dirty or different producer."""
    e4.producer_commit(producer, plan["commit"])
    root, manifest, digest = score.artifact(Path(train_dir).parent)
    require(manifest.kind == "evaluation" and manifest.name == "ste-rewriter-granite-train",
            "GRANITE-RECORD: completed Granite training evidence is required")
    raw = score.captured_file(root, manifest, "procedure.json")
    record = json.loads(raw)
    require(record["status"] == "completed" and "failure" not in record and
            profile.model_for(record) == profile.MODEL and record["commit"] == plan["commit"],
            "GRANITE-RECORD: producer procedure status, model or launch differs")
    require(record["producer_sha256"] == mf.sha256_file(Path(train_dir) / "run.json"),
            "GRANITE-RECORD: producer changed after completion")
    require(record["config_sha256"] == configuration(repo)[1] and
            record["training_build"]["build_sha256"] == plan["build_sha256"],
            "GRANITE-RECORD: producer config or build differs")
    provenance.verify_pair(record, repo, plan["commit"])
    require(record["epoch_adapters"] == e4.epoch_adapters(producer, train_dir) and
            producer.results.get("global_step") == 100,
            "GRANITE-R1: four saved adapters and 100 optimizer steps are required")
    training_padding(producer)
    return {"experiment": record["experiment"], "score_plans": record["score_plans"],
            "procedure_sha256": build.digest(raw), "procedure_manifest_sha256": digest}


def training_padding(producer):
    """GRANITE-VERIFY: completed R1 evidence must retain both ID observations."""
    padding = producer.results.get("padding_token_ids", {})
    profile.verify_padding_ids(padding.get("before"), loaded=False)
    profile.verify_padding_ids(padding.get("after"), loaded=True)


def seal_procedure(out, record, name):
    """GRANITE-RECORD: a sealing failure cannot leave a completed model procedure."""
    try:
        return e4.seal(out, name)
    except BaseException as exc:
        record.update(status="failed", failure={"type": type(exc).__name__, "message": str(exc)})
        (out / "procedure.json").write_bytes(e4.json_bytes(record))
        raise


def train(experiment_dir, build_dir, out_dir, *, baseline, repo=REPO):
    """GRANITE-R1, GRANITE-RECORD: train a fresh adapter and retain all four epochs."""
    from weaver_train import trainer
    from rewriter_r1_e4_report import loss_curve
    import torch
    from rewriter_granite_report import read_baseline

    experiment, identity = read_experiment(experiment_dir, repo=repo)
    _, baseline_id = read_baseline(baseline, score.read_plan(Path(experiment_dir) / "heldout", repo=repo))
    data, data_id = e4.training_build(build_dir)
    require(data == experiment["current_record"] and data_id == experiment["training_build"],
            "GRANITE-DATA: training input differs from the frozen build")
    imports = provenance.capture(repo)
    commit = imports["commit"]
    require(mf.git_commit() == commit, "GRANITE-RECORD: producer default commit differs before launch")
    _, config_sha = configuration(repo)
    path = score.loader.manifest_path_for(profile.MODEL)
    manifest = mf.load_tree(path)
    directory = score.loader.resolve_dir(manifest)
    memory, _ = probe_record(experiment["memory_directory"], commit, data["tree_hash"], repo)
    out, _ = score.fresh_score_output(out_dir, repo=repo,
                                     inputs=(experiment_dir, build_dir, directory, baseline))
    out.mkdir(parents=True)
    record = {"schema": "weaver-train/rewriter-granite-train/v1", "status": "started",
        **profile.IDENTITY, "commit": commit, "engine": "python-hf", "precision": "bf16",
        "created_at": datetime.now(timezone.utc).isoformat(), "experiment": identity,
        "score_plans": {sample: list(score.read_plan(Path(experiment_dir) / sample, repo=repo)[4:])
                        for sample in ("heldout", "training")},
        "training_build": data_id, "baseline": baseline_id,
        "config_sha256": config_sha, "imports_before": imports}
    started = time.monotonic()
    try:
        (out / "procedure.json").write_bytes(e4.json_bytes(record))
        profile.verify_model_files(manifest, directory)
        record["verification"] = verify_environment(out, repo)
        require(torch.cuda.is_available() and torch.cuda.device_count() == 2,
                "GRANITE-VERIFY: exactly two selected GPUs are required")
        producer = trainer.train(directory, path, Path(build_dir) / "train.jsonl", repo / CONFIG,
                                 out / "train", granite_probe_placement=memory["placement_before"])
        training_padding(producer)
        e4.producer_commit(producer, commit)
        record["epoch_adapters"] = e4.epoch_adapters(producer, out / "train")
        record["loss_curve"] = loss_curve(producer, json.loads(
            (out / "train/trainer/log_history.json").read_bytes()))
        record["producer_sha256"] = mf.sha256_file(out / "train/run.json")
        record["peak_memory_bytes"] = producer.results["peak_memory_bytes"]
        record["imports_after"] = provenance.capture(repo, previous=imports)
        require(read_experiment(experiment_dir, repo=repo)[1] == identity and
                e4.training_build(build_dir)[1] == data_id and
                read_baseline(baseline, score.read_plan(Path(experiment_dir) / "heldout", repo=repo))[1] == baseline_id,
                "GRANITE-RECORD: frozen inputs changed during training")
        record["status"] = "completed"
    except BaseException as exc:
        record.update(status="failed", failure={"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        record["wall_seconds"] = time.monotonic() - started
        (out / "procedure.json").write_bytes(e4.json_bytes(record))
    return record, seal_procedure(out, record, "ste-rewriter-granite-train")


def main(argv=None):
    """GRANITE-FILES: explicit commands never chain a probe, R0 and training."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    for name in ("build", "reference-build", "memory", "folded-prompt", "out"):
        plan.add_argument("--" + name, type=Path, required=True)
    training = commands.add_parser("train")
    for name in ("experiment", "build", "baseline", "out"):
        training.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "plan":
        record, manifest = freeze(args.build, args.reference_build, args.memory,
                                   args.folded_prompt, args.out)
    else:
        record, manifest = train(args.experiment, args.build, args.out, baseline=args.baseline)
    print(json.dumps({"status": record.get("status", "completed"), "tree_hash": manifest.tree_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
