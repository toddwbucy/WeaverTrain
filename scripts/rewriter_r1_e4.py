"""Spec 0.4 REWRITE-R1-E4: freeze and verify the separate four-epoch procedure."""

from __future__ import annotations

import argparse
import copy
import json
import random
import shutil
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "src")]

import rewriter_provenance as provenance
import rewriter_score as score

build, mf, require = score.build, score.mf, score.require
CONFIG = "config/rewriter-r1-e4.toml"
EPOCHS = (1, 2, 3, 4)


def json_bytes(value):
    """REWRITE-RECORD: use one explicit encoding for local JSON records."""
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode()


def ordered_hash(ids):
    """PAIR-BALANCE: hash the ordered JSON array without a final newline."""
    return build.digest(json.dumps(ids, ensure_ascii=True, separators=(",", ":")).encode())


def configuration(repo):
    """REWRITE-R1-E4: allow only the four-epoch change to the R1 configuration."""
    original = tomllib.loads((repo / "config/rewriter-train.toml").read_text())
    raw = (repo / CONFIG).read_bytes()
    current = tomllib.loads(raw.decode())
    expected = copy.deepcopy(original)
    expected["train"]["num_train_epochs"] = 4
    require(original["train"]["num_train_epochs"] == 2 and current == expected,
            "REWRITE-R1-E4: configuration differs beyond the epoch count")
    return current, build.digest(raw)


def training_build(directory):
    """REWRITE-R1-E4: capture the complete build and its training bytes."""
    root, manifest, manifest_sha = score.artifact(directory)
    require(manifest.kind == "dataset" and manifest.name == "ste-rewriter-build",
            "REWRITE-R1-E4: expected a rewriter training build")
    raw = score.captured_file(root, manifest, "build.json")
    record = json.loads(raw)
    require(record["schema"] == "weaver-train/rewriter-build/v1" and
            record["status"] == "completed" and record["render_only"] is False,
            "REWRITE-R1-E4: training build is not completed")
    identity = {"tree_hash": manifest.tree_hash, "manifest_sha256": manifest_sha,
                "build_sha256": build.digest(raw),
                "train_sha256": build.digest(score.captured_file(root, manifest, "train.jsonl"))}
    return record, identity


def approved_ids(selection, repo):
    """REWRITE-R1-E4: bind the pair order to the unchanged R1 selection pin."""
    pins = tomllib.loads((repo / "config/rewriter-inputs.toml").read_text())
    ids = selection["ids"]
    require(len(ids) == 200 and len({tuple(item) for item in ids}) == 200 and
            ordered_hash(ids) == selection["ordered_ids_sha256"] == pins["r1_ordered_ids"],
            "REWRITE-R1-E4: approved training IDs or order differ")
    return ids


def verify_build(current, original, current_id, original_id, commit, repo):
    """REWRITE-R1-E4: verify a new record with identical R1 training bytes."""
    require(current["procedure"] == "r1-e4" and original["procedure"] == "r1",
            "REWRITE-R1-E4: training build procedure differs")
    require(current.get("commit") == commit,
            "REWRITE-R1-E4: training build launch commit differs")
    approved_ids(current["selection"], repo)
    require(current["selection"] == original["selection"],
            "REWRITE-R1-E4: training selection differs from original R1")
    require(current["counts"] == original["counts"],
            "REWRITE-R1-E4: training token counts differ from original R1")
    require(current_id["train_sha256"] == original_id["train_sha256"],
            "REWRITE-R1-E4: training bytes differ from original R1")
    require(current["tokens_per_epoch"] == [dict(epoch=n, **current["counts"])
                                            for n in EPOCHS],
            "REWRITE-R1-E4: four epoch token records are required")
    score.require_fields(current, original)
    for key in ("folded_prompt", "prompt_provenance"):
        require(current[key] == original[key],
                f"REWRITE-R1-E4: training {key} differs from original R1")


def training_kit(selection, repo):
    """REWRITE-R1-E4-TRAIN-SCORES: select only source text from the approved pairs."""
    ids = approved_ids(selection, repo)
    chosen = random.Random(2034).sample(ids, 50)
    evidence = build.Evidence(repo)
    pins = tomllib.loads(evidence.read("config/rewriter-inputs.toml").decode())
    sources = {}
    for batch, pin in (("200", "source_200"), ("200-2", "source_200_2")):
        records = evidence.rows(f"var/ste/batch-{batch}/source.jsonl", pins[pin])
        sources.update({(batch, key): entry.data for key, entry in build.unique(records).items()})
    rows = []
    for batch, segment in chosen:
        require((batch, segment) in sources,
                "REWRITE-R1-E4-TRAIN-SCORES: selected source is absent")
        source = sources[(batch, segment)]
        lead, body, trail = score.ste_convert.split_ws(source["raw"])
        text, codes = score.ste_convert.mask(body)
        rows.append({"id": f"{batch}/{segment}", "batch": batch, "segment_id": segment,
                     "uuid": source["uuid"], "kind": source["kind"], "loc": source["loc"],
                     "raw": body, "lead": lead, "trail": trail, "text": text,
                     "codes": codes, "bin": score.bin_of(len(text))})
    inputs = [{key: row[key] for key in ("id", "kind", "text")} for row in rows]
    evidence.verify()
    return {"rows": rows, "inputs": evidence.files, "source_seed": 2034,
            "selected_ids": chosen, "ordered_ids_sha256": ordered_hash(chosen),
            "input_sha256": build.digest(score.jsonl_bytes(inputs))}


def same_evaluation(candidate, reference):
    """REWRITE-R1-E4-SCORES: compare values across different clean record identities."""
    plan, kit, entries, folded = candidate[:4]
    old, old_kit, old_entries, old_folded = reference[:4]
    for key in (*score.FIELDS, "inputs", "environment_sha256", "input_sha256",
                "folded_prompt", "prompt_provenance", "sampler", "denominator",
                "source_seed", "review_sample_seed", "review_sample", "reviewer_model",
                "reviewer_prompt_sha256"):
        require(plan[key] == old[key], f"REWRITE-R1-E4-SCORES: evaluation {key} differs")
    require(kit == old_kit, "REWRITE-R1-E4-SCORES: source text or code maps differ")
    require(entries == old_entries, "REWRITE-R1-E4-SCORES: prompt tokens or budgets differ")
    require(folded == old_folded, "REWRITE-R1-E4-SCORES: prompt bytes differ")


def seal(directory, name):
    """REWRITE-RECORD: seal a completed new artifact without replacing a prior one."""
    manifest = mf.build_tree(directory, "evaluation", name)
    with directory.with_name(directory.name + ".manifest.json").open("x") as stream:
        stream.write(manifest.to_json())
    return manifest


def freeze(build_dir, original_build, reference_plan, folded, out_dir, *, repo=REPO):
    """REWRITE-R1-E4: freeze both score plans and their shared training identity."""
    commit = provenance.clean_commit(repo)
    _, config_sha = configuration(repo)
    current, current_id = training_build(build_dir)
    original, original_id = training_build(original_build)
    verify_build(current, original, current_id, original_id, commit, repo)
    old_bundle = score.read_plan(reference_plan, repo=repo)
    require(old_bundle[0]["build_sha256"] == original_id["build_sha256"],
            "REWRITE-R1-E4: original plan names a different R1 build")
    out, _ = score.fresh_score_output(out_dir, repo=repo,
                                    inputs=(build_dir, original_build, reference_plan))
    out.mkdir(parents=True)
    score.freeze_plan(build_dir, folded, out / "heldout", repo=repo)
    bundle = score.read_plan(out / "heldout", repo=repo)
    same_evaluation(bundle, old_bundle)
    kit = training_kit(current["selection"], repo)
    evidence = build.Evidence(repo)
    tokenizer, model_manifest, model_dir, _ = build.build_tokenizer(evidence)
    entries = score.preflight(tokenizer, kit, bundle[3], repo=repo, expected_count=50)
    plan = {**bundle[0], "schema": "weaver-train/rewriter-training-plan/v1",
            "denominator": 50, "source_seed": 2034, "review_sample": [],
            "input_sha256": kit["input_sha256"], "training_selection": current["selection"],
            "selected_ids": kit["selected_ids"], "ordered_ids_sha256": kit["ordered_ids_sha256"]}
    train_plan = out / "training"
    train_plan.mkdir()
    inputs = [{key: row[key] for key in ("id", "kind", "text")} for row in kit["rows"]]
    for name, raw in {"plan.json": json_bytes(plan), "kit.json": json_bytes(kit),
                      "entries.jsonl": score.jsonl_bytes(entries),
                      "input.jsonl": score.jsonl_bytes(inputs), "folded.txt": bundle[3]}.items():
        (train_plan / name).write_bytes(raw)
    seal(train_plan, "ste-rewriter-training-plan")
    shutil.copytree(reference_plan, out / "reference")
    source_manifest = Path(reference_plan).with_name(Path(reference_plan).name + ".manifest.json")
    shutil.copyfile(source_manifest, out / "reference.manifest.json")
    record = {"schema": "weaver-train/rewriter-r1-e4-plan/v1", "commit": commit,
              "config_sha256": config_sha, "training_build": current_id,
              "original_build": original_id, "original_record": original,
              "current_record": current, "reference_plan_tree": old_bundle[4],
              "reference_plan_manifest_sha256": old_bundle[5],
              "sample_seed": 2034, "selected_ids": kit["selected_ids"],
              "ordered_ids_sha256": kit["ordered_ids_sha256"]}
    (out / "experiment.json").write_bytes(json_bytes(record))
    evidence.verify()
    mf.require_tree(model_manifest, model_dir)
    require(provenance.clean_commit(repo) == commit,
            "REWRITE-R1-E4: checkout changed while freezing both plans")
    manifest = seal(out, "ste-rewriter-r1-e4-plan")
    read_experiment(out, repo=repo)
    return record, manifest


def read_training_plan(directory, manifest, manifest_sha, repo):
    """REWRITE-R1-E4-TRAIN-SCORES: rederive the source-only sample on every read."""
    raw = {name: score.captured_file(directory, manifest, name) for name in
           ("plan.json", "kit.json", "entries.jsonl", "input.jsonl", "folded.txt")}
    plan, kit = json.loads(raw["plan.json"]), json.loads(raw["kit.json"])
    require(manifest.kind == "evaluation" and
            plan["schema"] == "weaver-train/rewriter-training-plan/v1" and
            plan["denominator"] == 50 and plan["source_seed"] == 2034,
            "REWRITE-R1-E4-TRAIN-SCORES: plan schema, count or seed differs")
    expected = training_kit(plan["training_selection"], repo)
    require(kit == expected and plan["selected_ids"] == expected["selected_ids"] and
            plan["ordered_ids_sha256"] == expected["ordered_ids_sha256"],
            "REWRITE-R1-E4-TRAIN-SCORES: selected source sequence or hash differs")
    inputs = [{key: row[key] for key in ("id", "kind", "text")} for row in kit["rows"]]
    require(raw["input.jsonl"] == score.jsonl_bytes(inputs) and
            plan["input_sha256"] == kit["input_sha256"],
            "REWRITE-R1-E4-TRAIN-SCORES: masked source inputs differ")
    evidence, pins, prompt = score.score_evidence(repo)
    require(plan["inputs"] == evidence.files and plan["prompt_provenance"] == prompt and
            plan["folded_prompt"] == pins["folded_prompt"] and
            plan["environment_sha256"] == evidence.files["environment.lock"]["sha256"],
            "REWRITE-R1-E4-TRAIN-SCORES: frozen evaluation identities differ")
    build.folded_system(raw["folded.txt"], pins["folded_prompt"])
    entries = [json.loads(line) for line in raw["entries.jsonl"].splitlines()]
    require([row["id"] for row in entries] == [row["id"] for row in kit["rows"]],
            "REWRITE-R1-E4-TRAIN-SCORES: prompt membership differs")
    evidence.verify()
    return plan, kit, entries, raw["folded.txt"], manifest.tree_hash, manifest_sha


def read_experiment(directory, *, repo=REPO):
    """REWRITE-R1-E4: verify both plans before any training or score procedure."""
    root, manifest, manifest_sha = score.artifact(directory)
    require(manifest.kind == "evaluation" and manifest.name == "ste-rewriter-r1-e4-plan",
            "REWRITE-R1-E4: expected a frozen four-epoch experiment")
    record = json.loads(score.captured_file(root, manifest, "experiment.json"))
    require(record["schema"] == "weaver-train/rewriter-r1-e4-plan/v1" and
            record["commit"] == provenance.clean_commit(repo),
            "REWRITE-R1-E4: experiment launch commit differs")
    require(record["config_sha256"] == configuration(repo)[1],
            "REWRITE-R1-E4: frozen configuration hash differs")
    verify_build(record["current_record"], record["original_record"],
                 record["training_build"], record["original_build"], record["commit"], repo)
    heldout = score.read_plan(root / "heldout", repo=repo)
    training = score.read_plan(root / "training", repo=repo)
    reference = score.read_plan(root / "reference", repo=repo)
    same_evaluation(heldout, reference)
    require(reference[4:] == (record["reference_plan_tree"],
                             record["reference_plan_manifest_sha256"]),
            "REWRITE-R1-E4: frozen reference identity differs")
    for bundle in (heldout, training):
        plan = bundle[0]
        require(plan["commit"] == record["commit"] and
                plan["build_sha256"] == record["training_build"]["build_sha256"] and
                plan["build_tree_hash"] == record["training_build"]["tree_hash"] and
                plan["build_manifest_sha256"] == record["training_build"]["manifest_sha256"],
                "REWRITE-R1-E4: score plan training identity differs")
    require(record["sample_seed"] == 2034 and
            record["selected_ids"] == training[1]["selected_ids"] and
            record["ordered_ids_sha256"] == training[1]["ordered_ids_sha256"],
            "REWRITE-R1-E4: frozen training sample identity differs")
    for key in (*score.FIELDS, "inputs", "environment_sha256", "folded_prompt",
                "prompt_provenance", "sampler", "reviewer_model", "reviewer_prompt_sha256"):
        require(training[0][key] == heldout[0][key],
                f"REWRITE-R1-E4-TRAIN-SCORES: {key} differs from held-out plan")
    require(training[3] == heldout[3] and training[0]["review_sample"] == [],
            "REWRITE-R1-E4-TRAIN-SCORES: prompt bytes or review selection differ")
    return record, {"tree_hash": manifest.tree_hash, "manifest_sha256": manifest_sha}


def score_plan(plan_dir, bundle, repo):
    """REWRITE-R1-E4: bind a score plan to the pre-training experiment seal."""
    plan_dir = Path(plan_dir).resolve()
    _, identity = read_experiment(plan_dir.parent, repo=repo)
    require(plan_dir.name in ("heldout", "training") and
            bundle[0]["denominator"] == (100 if plan_dir.name == "heldout" else 50),
            "REWRITE-R1-E4: score plan is not an experiment cohort")
    return {**identity, "sample": plan_dir.name}


def verify_score_proof(proof, bundle, experiment):
    """REWRITE-R1-E4: the scored plan must be the one frozen before training."""
    sample = "heldout" if bundle[0]["denominator"] == 100 else "training"
    require(experiment == {**proof["experiment"], "sample": sample} and
            proof["score_plans"][sample] == list(bundle[4:]),
            "REWRITE-R1-E4: score differs from the pre-training plan")


def producer_commit(producer, commit):
    """REWRITE-R1-E4: reject absent, dirty or different producer identities."""
    require(isinstance(producer.commit, dict) and
            producer.commit.get("dirty") is False and
            isinstance(producer.commit.get("rev"), str) and bool(producer.commit["rev"]) and
            producer.commit == commit,
            "REWRITE-R1-E4: training producer commit differs from clean launch")


def epoch_adapters(producer, train_dir):
    """REWRITE-R1-E4: verify all four saved epoch trees without substituting a final adapter."""
    epochs = producer.results.get("epoch_adapters", [])
    require([entry["epoch"] for entry in epochs] == list(EPOCHS) and
            all(type(entry["epoch"]) is int for entry in epochs),
            "REWRITE-R1-E4: producer must retain four distinct epoch adapters")
    identities = []
    for entry in epochs:
        _, manifest, manifest_sha = score.artifact(Path(train_dir) / f"adapter-epoch-{entry['epoch']}")
        require(manifest.kind == "model" and entry["tree_hash"] == manifest.tree_hash,
                "REWRITE-R1-E4: saved epoch adapter differs from producer")
        identities.append({**entry, "manifest_sha256": manifest_sha})
    return identities


def train(experiment_dir, build_dir, out_dir, *, repo=REPO):
    """REWRITE-R1-E4: call the unchanged trainer with verified launch provenance."""
    from weaver_train import trainer
    import torch

    started = time.monotonic()
    experiment, identity = read_experiment(experiment_dir, repo=repo)
    data, data_id = training_build(build_dir)
    require(data == experiment["current_record"] and data_id == experiment["training_build"],
            "REWRITE-R1-E4: training input differs from the frozen experiment")
    imports = provenance.capture(repo)
    commit = imports["commit"]
    require(mf.git_commit() == commit,
            "REWRITE-R1-E4: producer default commit differs before training")
    _, config_sha = configuration(repo)
    model_manifest_path = score.loader.manifest_path_for(build.MODEL)
    model_manifest = mf.load_tree(model_manifest_path)
    model_dir = score.loader.resolve_dir(model_manifest)
    require(model_manifest.tree_hash == data["tree_hash"],
            "REWRITE-R1-E4: training model differs from the frozen experiment")
    out, _ = score.fresh_score_output(out_dir, repo=repo,
                                     inputs=(experiment_dir, build_dir, model_dir))
    out.mkdir(parents=True)
    plans = {sample: list(score.read_plan(Path(experiment_dir) / sample, repo=repo)[4:])
             for sample in ("heldout", "training")}
    record = {"schema": "weaver-train/rewriter-r1-e4-train/v1", "status": "started",
              "created_at": datetime.now(timezone.utc).isoformat(), "commit": commit,
              "engine": "python-hf", "precision": "bf16", "experiment": identity,
              "score_plans": plans, "training_build": data_id,
              "config_sha256": config_sha, "imports_before": imports}
    devices = []

    def save():
        """REWRITE-RECORD: preserve a failure without sealing it as completed."""
        (out / "procedure.json").write_bytes(json_bytes(record))

    save()
    try:
        require(torch.cuda.is_available() and torch.cuda.device_count() == 2,
                "REWRITE-R1-E4: exactly two selected GPUs are required for training")
        torch.cuda.init()
        devices = list(range(2))
        for device in devices:
            torch.cuda.reset_peak_memory_stats(device)
        record["gpus"] = {str(device): {"name": torch.cuda.get_device_name(device),
            "capability": list(torch.cuda.get_device_capability(device))} for device in devices}
        producer = trainer.train(model_dir, model_manifest_path, Path(build_dir) / "train.jsonl",
                                 repo / CONFIG, out / "train")
        producer_commit(producer, commit)
        record["epoch_adapters"] = epoch_adapters(producer, out / "train")
        from rewriter_r1_e4_report import loss_curve

        record["loss_curve"] = loss_curve(producer, json.loads(
            (out / "train/trainer/log_history.json").read_bytes()))
        record["producer_sha256"] = mf.sha256_file(out / "train/run.json")
        record["imports_after"] = provenance.capture(repo, previous=imports)
        require(read_experiment(experiment_dir, repo=repo)[1] == identity and
                training_build(build_dir)[1] == data_id,
                "REWRITE-R1-E4: frozen inputs changed during training")
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
        save()
    try:
        manifest = seal(out, "ste-rewriter-r1-e4-train")
    except BaseException as exc:
        record["status"] = "failed"
        record["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        save()
        raise
    return record, manifest


def producer_proof(train_dir, producer, plan, repo):
    """REWRITE-R1-E4: require the new producer and its complete launch evidence."""
    producer_commit(producer, plan["commit"])
    root, manifest, manifest_sha = score.artifact(Path(train_dir).parent)
    require(manifest.kind == "evaluation" and manifest.name == "ste-rewriter-r1-e4-train",
            "REWRITE-R1-E4: completed training procedure evidence is required")
    raw = score.captured_file(root, manifest, "procedure.json")
    record = json.loads(raw)
    require(record["schema"] == "weaver-train/rewriter-r1-e4-train/v1" and
            record["status"] == "completed" and "failure" not in record and
            record["commit"] == plan["commit"],
            "REWRITE-R1-E4: training procedure record differs from clean launch")
    require(record["producer_sha256"] == mf.sha256_file(Path(train_dir) / "run.json"),
            "REWRITE-R1-E4: training producer changed after completion")
    require(record["config_sha256"] == configuration(repo)[1] and
            record["training_build"]["build_sha256"] == plan["build_sha256"],
            "REWRITE-R1-E4: training procedure config or build differs")
    provenance.verify_pair(record, repo, plan["commit"])
    require(record["epoch_adapters"] == epoch_adapters(producer, train_dir),
            "REWRITE-R1-E4: training procedure epoch identities differ")
    require(producer.results.get("global_step") == 100,
            "REWRITE-R1-E4: producer does not record 100 optimizer steps")
    return {"experiment": record["experiment"], "score_plans": record["score_plans"],
            "procedure_sha256": build.digest(raw), "procedure_manifest_sha256": manifest_sha}


def main(argv=None):
    """REWRITE-R1-E4: expose preparation separately from model procedures."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    for arg in ("build", "original-build", "reference-plan", "folded-prompt", "out"):
        plan.add_argument("--" + arg, type=Path, required=True)
    training = commands.add_parser("train")
    for arg in ("experiment", "build", "out"):
        training.add_argument("--" + arg, type=Path, required=True)
    experts = commands.add_parser("experts")
    experts.add_argument("--out", type=Path, required=True)
    result = commands.add_parser("report")
    for arg in ("experiment", "build", "train", "r0-first", "r0-second", "r1-score",
                "r1-adapter", "r1-build", "review-batch", "review-result", "inventory", "out"):
        result.add_argument("--" + arg, type=Path, required=True)
    result.add_argument("--heldout", type=Path, nargs=4, required=True)
    result.add_argument("--training", type=Path, nargs=4, required=True)
    args = parser.parse_args(argv)
    if args.command == "plan":
        _, manifest = freeze(args.build, args.original_build, args.reference_plan,
                             args.folded_prompt, args.out)
    elif args.command == "train":
        _, manifest = train(args.experiment, args.build, args.out)
    elif args.command == "experts":
        from rewriter_r1_e4_report import inventory

        _, manifest = inventory(args.out)
    else:
        from rewriter_r1_e4_report import write_report

        _, manifest = write_report(args)
    print(json.dumps({"tree_hash": manifest.tree_hash}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
