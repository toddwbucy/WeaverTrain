"""GRANITE-TESTS: separate series, frozen sources, producer proofs and blind review."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import rewriter_granite as granite
import rewriter_granite_profile as profile
import rewriter_granite_report as dense_report
import rewriter_r1_e4_report as measurements
import rewriter_report as report
import rewriter_review as review
from tests.test_rewriter_r1_e4 import (
    command, commit_fixture, curve_fixture, experiment, save_build, tokenizer, world,
)

score, mf, e4 = granite.score, granite.mf, granite.e4


@pytest.fixture
def dense(experiment, monkeypatch):
    """GRANITE-DATA: retain real file hashes while replacing only model I/O."""
    c = experiment
    (c.repo / granite.CONFIG).write_bytes((granite.REPO / granite.CONFIG).read_bytes())
    with (c.repo / "config/models.toml").open("a") as stream:
        stream.write(f'\n[models."{profile.MODEL}"]\nrepo_id = "fixture"\nrevision = "fixture-revision"\n')
    commit_fixture(c.repo)
    c.commit = mf.git_commit(c.repo)
    original = json.loads((c.build / "build.json").read_bytes())
    original.update(pair_manifest={"fixture": "same source pairs"},
        pair_manifest_sha256="a" * 64, source_dataset="fixture", source_revision="fixture",
        limits={"repair_training_allowed": False})
    save_build(c.build, original)
    current = copy.deepcopy(original)
    current.update(**profile.IDENTITY, procedure=profile.R1, commit=c.commit)
    # Synthetic tokenizer fixtures have their own template pin. Pinned bytes have a separate test.
    monkeypatch.setattr(profile, "TEMPLATE", current["template_hash"])
    monkeypatch.setattr(profile, "verify_tokenizer", lambda tok: None)
    monkeypatch.setattr(profile, "verify_render", lambda *args: None)
    monkeypatch.setattr(profile, "verify_model_files", lambda *args: None)
    monkeypatch.setattr(granite, "require_cache", lambda *args: None)
    monkeypatch.setattr(granite, "loaded_placement", lambda model: {"attention_implementation": "fixture"})
    monkeypatch.setattr(granite, "verify_environment", lambda *args: {"status": "completed"})
    current["tokens_per_epoch"] = [dict(epoch=n, **current["counts"]) for n in e4.EPOCHS]
    imports = {"commit": c.commit, "modules": {}, "weaver_train_file": "fixture"}
    monkeypatch.setattr(granite.provenance, "capture", lambda *a, **kw: imports)
    monkeypatch.setattr(granite.provenance, "verify_pair", lambda *a: None)
    old_commit = mf.git_commit
    monkeypatch.setattr(mf, "git_commit", lambda repo=None: c.commit if repo is None else old_commit(repo))
    current.update(imports_before=imports, imports_after=imports)
    c.granite_build = c.build.with_name("granite-build")
    save_build(c.granite_build, current)
    old_manifest_path = score.loader.manifest_path_for(score.build.MODEL)
    manifest = mf.load_tree(old_manifest_path)
    manifest.name = profile.MODEL
    c.model_manifest = old_manifest_path.with_name("granite-model.json")
    manifest.save(c.model_manifest)
    monkeypatch.setattr(score.loader, "manifest_path_for", lambda name:
                        c.model_manifest if name == profile.MODEL else old_manifest_path)
    old_tokenizer = score.build.build_tokenizer
    monkeypatch.setattr(score.build, "build_tokenizer", lambda evidence, model=score.build.MODEL:
        (c.loaded.tokenizer, manifest, c.model, manifest.tree_hash) if model == profile.MODEL else
        old_tokenizer(evidence))
    c.memory = c.build.with_name("granite-memory")
    c.memory.mkdir()
    memory = {"schema": "weaver-train/rewriter-granite-probe/v1", "status": "completed",
        **profile.IDENTITY, "commit": c.commit, "tree_hash": manifest.tree_hash,
        "config_sha256": granite.configuration(c.repo)[1], "sequence_length": 49152,
        "finite_gradients": True, "trainable_parameters": 140509184,
        "placement_before": {"attention_implementation": "fixture"},
        "target_inventory": [{"fixture": i} for i in range(448)],
        "imports_before": imports, "imports_after": imports}
    (c.memory / "procedure.json").write_bytes(e4.json_bytes(memory))
    e4.seal(c.memory, "ste-rewriter-granite-probe")
    kit = e4.training_kit(current["selection"], c.repo)
    monkeypatch.setattr(granite, "TRAINING_SAMPLE", kit["ordered_ids_sha256"])
    c.dense_experiment = c.build.with_name("granite-experiment")
    c.experiment_record, _ = granite.freeze(c.granite_build, c.build, c.memory, c.folded,
                                            c.dense_experiment, repo=c.repo)
    return c


def test_granite_plans_keep_sources_but_new_model_identity(dense):
    c = dense
    record, _ = granite.read_experiment(c.dense_experiment, repo=c.repo)
    heldout = score.read_plan(c.dense_experiment / "heldout", repo=c.repo)
    training = score.read_plan(c.dense_experiment / "training", repo=c.repo)
    assert record["current_record"]["selection"] == record["reference_record"]["selection"]
    assert [heldout[0]["denominator"], training[0]["denominator"]] == [100, 50]
    assert heldout[0]["model"] == training[0]["model"] == profile.MODEL
    assert len(heldout[0]["review_sample"]) == 30
    assert training[0]["review_sample"] == []
    assert all("target" not in row for row in training[1]["rows"])
    assert len(training[0]["selected_ids"]) == 50


@pytest.mark.parametrize("sample,procedure", [("heldout", "r0"), ("training", "r1-e4")])
def test_granite_plan_rejects_nemotron_procedure(dense, sample, procedure):
    c = dense
    options = {} if procedure == "r0" else dict(epoch=1, adapter=c.build, training_build=c.build)
    with pytest.raises(score.ScoreError, match="score procedure and model series differ"):
        score.run_score(c.dense_experiment / sample, c.out, procedure=procedure, repo=c.repo, **options)
    assert not c.calls and not c.decoded


@pytest.mark.parametrize("field", ["selection", "pair_manifest", "pair_manifest_sha256",
    "source_dataset", "source_revision", "folded_prompt", "prompt_provenance", "limits"])
def test_granite_build_source_guards(dense, field):
    c = dense
    current = copy.deepcopy(c.experiment_record["current_record"])
    original = copy.deepcopy(c.experiment_record["reference_record"])
    original[field] = "different"
    with pytest.raises(score.ScoreError, match=f"source {field} differs"):
        granite.verify_build(current, original, c.commit, c.repo)


@pytest.mark.parametrize("field,value,reason", [
    ("procedure", "r1-e4", "Granite training build"),
    ("commit", {"rev": "different", "dirty": False}, "training build launch"),
    ("template_hash", "wrong", "training render fields"),
    ("tools", True, "training render fields"),
    ("enable_thinking", True, "training render fields"),
    ("max_seq_length", 32768, "training render fields"),
    ("tokens_per_epoch", [], "four complete training token records"),
])
def test_granite_build_record_guards(dense, field, value, reason):
    c = dense
    current = copy.deepcopy(c.experiment_record["current_record"])
    current[field] = value
    with pytest.raises(ValueError, match=reason):
        granite.verify_build(current, c.experiment_record["reference_record"], c.commit, c.repo)


def edit_artifact(directory, filename, change):
    """GRANITE-TESTS: keep the checksum guard valid to reach the semantic guard."""
    manifest_path = directory.with_name(directory.name + ".manifest.json")
    manifest = mf.load_tree(manifest_path)
    path = directory / filename
    record = json.loads(path.read_bytes())
    change(record)
    path.write_bytes(e4.json_bytes(record))
    mf.build_tree(directory, manifest.kind, manifest.name).save(manifest_path)


@pytest.mark.parametrize("field,value,reason", [
    ("status", "failed", "probe model, launch or status"),
    ("tree_hash", "wrong", "probe model, launch or status"),
    ("sequence_length", 32768, "exact context, targets and training path"),
    ("finite_gradients", False, "exact context, targets and training path"),
    ("trainable_parameters", 6648832, "exact context, targets and training path"),
    ("target_inventory", [], "exact context, targets and training path"),
    ("config_sha256", "wrong", "exact context, targets and training path"),
])
def test_memory_record_guards(dense, field, value, reason):
    c = dense
    edit_artifact(c.memory, "procedure.json", lambda r: r.update({field: value}))
    with pytest.raises(ValueError, match=reason):
        granite.probe_record(c.memory, c.commit, c.loaded.tree_hash, c.repo)


def verdicts_for(c, sources, label):
    """GRANITE-REVIEW: feed synthetic faithful verdicts through the real ingest CLI."""
    plan = c.dense_experiment / "heldout"
    batch = c.build.with_name(label + "-batch")
    record, _ = review.prepare(plan, sources, batch, repo=c.repo)
    audit = json.loads((batch / "private.json").read_bytes())
    blind = [json.loads(line) for line in (batch / "reviewer/input.jsonl").read_bytes().splitlines()]
    assert all(set(row) == {"review_id", "source", "rewrite"} for row in blind)
    assert audit["rewriter_model"] == profile.MODEL
    external = c.build.with_name(label + "-verdicts.jsonl")
    external.write_bytes(score.jsonl_bytes([
        {"review_id": row["review_id"], "verdict": "faithful", "defects": []} for row in blind]))
    result = c.build.with_name(label + "-review-result")
    args = ["ingest", "--plan", str(plan), "--batch", str(batch),
        "--verdicts", str(external), "--out", str(result),
        "--input-sha256", audit["input_sha256"], "--prompt-sha256", audit["prompt_sha256"],
        "--verdicts-sha256", mf.sha256_file(external)]
    for path, adapter, build in sources:
        args += (["--run", str(path)] if adapter is None else
                 ["--candidate", str(path), str(adapter), str(build)])
    assert review.main(args, repo=c.repo) == 0
    return batch, result, record


@pytest.fixture
def baseline(dense):
    c = dense
    c.r0 = [c.build.with_name(f"granite-r0-{n}") for n in (1, 2)]
    for n, path in enumerate(c.r0, start=1):
        score.run_score(c.dense_experiment / "heldout", path, procedure=profile.R0,
                        r0_pass=n, repo=c.repo)
    c.r0_batch, c.r0_review, _ = verdicts_for(c, [(c.r0[0], None, None)], "granite-r0")
    c.baseline = c.build.with_name("granite-baseline")
    dense_report.baseline(c.dense_experiment / "heldout", *c.r0, c.r0_batch, c.r0_review,
                          c.baseline, repo=c.repo)
    return c


def test_granite_baseline_binds_two_passes_and_blind_first_outputs(baseline):
    c = baseline
    bundle = score.read_plan(c.dense_experiment / "heldout", repo=c.repo)
    record, _ = dense_report.read_baseline(c.baseline, bundle)
    assert record["spread"]["denominator"] == 100
    assert record["spread"]["decode_difference"] == 0
    assert len(record["meaning_counts"]) == 1
    assert next(iter(record["meaning_counts"].values()))["reviewed"] == 30
    with pytest.raises(score.ScoreError, match="only the first R0 outputs"):
        review.prepare(c.dense_experiment / "heldout", [(c.r0[1], None, None)],
                        c.build.with_name("wrong-review"), repo=c.repo)


@pytest.fixture
def trained(baseline, monkeypatch):
    """GRANITE-R1: use synthetic model I/O, with production provenance and file readers."""
    from weaver_train import trainer
    from peft import PeftModel

    c = baseline
    monkeypatch.setattr(PeftModel, "from_pretrained", lambda model, *a, **kw: model)
    monkeypatch.setattr(score.loader, "pin_layer_devices", lambda model: 0)

    def training(model, manifest, dataset, config, out, **kwargs):
        out.mkdir()
        adapters = []
        for epoch in e4.EPOCHS:
            adapter = out / f"adapter-epoch-{epoch}"
            adapter.mkdir()
            (adapter / "adapter_config.json").write_bytes(b"{}\n")
            (adapter / "adapter_model.safetensors").write_bytes(f"fixture epoch {epoch}".encode())
            tree = mf.build_tree(adapter, "model", "fixture-granite-adapter")
            tree.save(adapter.with_name(adapter.name + ".manifest.json"))
            adapters.append({"epoch": epoch, "tree_hash": tree.tree_hash})
        curve, logs = curve_fixture()
        producer = mf.RunRecord(kind="train", seed=0, status="completed", commit=c.commit,
            model={"name": profile.MODEL, "tree_hash": c.loaded.tree_hash},
            dataset={"sha256": mf.sha256_file(dataset),
                "build_sha256": mf.sha256_file(dataset.parent / "build.json"),
                "rows": 200, "assistant_tokens": 400}, config=granite.configuration(c.repo)[0],
            results={"epoch_adapters": adapters, **curve.results, "epochs": 4,
                "peak_memory_bytes": {str(i): {"allocated": 100, "reserved": 200} for i in (0, 1)}})
        producer.save(out / "run.json")
        (out / "trainer").mkdir()
        (out / "trainer/log_history.json").write_bytes(e4.json_bytes(logs))
        return producer

    monkeypatch.setattr(trainer, "train", training)
    c.procedure = c.build.with_name("granite-training")
    c.training_record, _ = granite.train(c.dense_experiment, c.granite_build,
                                          c.procedure, baseline=c.baseline, repo=c.repo)
    return c


@pytest.fixture
def scored(trained):
    c = trained
    c.heldout, c.training, c.sources = [], [], []
    for epoch in e4.EPOCHS:
        adapter = c.procedure / f"train/adapter-epoch-{epoch}"
        for sample, destination in (("heldout", c.heldout), ("training", c.training)):
            path = c.build.with_name(f"granite-{sample}-{epoch}")
            score.run_score(c.dense_experiment / sample, path, procedure=profile.R1,
                epoch=epoch, adapter=adapter, training_build=c.granite_build, repo=c.repo)
            destination.append(path)
        c.sources.append((c.heldout[-1], adapter, c.granite_build))
    return c


def test_granite_four_epoch_report_and_blind_ingest(scored):
    c = scored
    batch, verdicts, record = verdicts_for(c, c.sources, "granite-epochs")
    assert record["available"] == 120
    args = SimpleNamespace(experiment=c.dense_experiment, build=c.granite_build, train=c.procedure,
        baseline=c.baseline, r0_first=c.r0[0], r0_second=c.r0[1], review_batch=batch,
        review_result=verdicts, heldout=c.heldout, training=c.training,
        out=c.build.with_name("granite-final"))
    result, _ = dense_report.write_report(args, repo=c.repo)
    assert len(result["loss_curve"]["steps"]) == 100
    assert [r["steps"] for r in result["loss_curve"]["epochs"]] == [25] * 4
    assert [r["epoch"] for r in result["epochs"]] == [1, 2, 3, 4]
    for epoch in result["epochs"]:
        assert epoch["heldout"]["checks"]["denominator"] == 100
        assert epoch["training"]["checks"]["denominator"] == 50
        assert epoch["meaning"]["reviewed"] == 30
        assert epoch["paired"]["reference"] == "r0_first"
        assert "r1_epoch_2" not in epoch["paired"]
    assert len((args.out / "loss.csv").read_text().splitlines()) == 101


@pytest.mark.parametrize("field,value,reason", [
    ("status", "failed", "producer procedure status, model or launch"),
    ("producer_sha256", "wrong", "producer changed after completion"),
    ("config_sha256", "wrong", "producer config or build"),
    ("epoch_adapters", [], "four saved adapters and 100 optimizer steps"),
])
def test_granite_producer_record_guards(trained, field, value, reason):
    c = trained
    edit_artifact(c.procedure, "procedure.json", lambda r: r.update({field: value}))
    producer = mf.load_run(c.procedure / "train/run.json")
    bundle = score.read_plan(c.dense_experiment / "heldout", repo=c.repo)
    with pytest.raises(score.ScoreError, match=reason):
        granite.producer_proof(c.procedure / "train", producer, bundle[0], c.repo)


@pytest.mark.parametrize("value", [None, {"rev": "wrong", "dirty": False}, {"rev": "wrong", "dirty": True}])
def test_granite_rejects_absent_dirty_or_other_producer(trained, value):
    c = trained
    producer = mf.load_run(c.procedure / "train/run.json")
    producer.commit = value
    bundle = score.read_plan(c.dense_experiment / "heldout", repo=c.repo)
    with pytest.raises(score.ScoreError, match="training producer commit differs from clean launch"):
        granite.producer_proof(c.procedure / "train", producer, bundle[0], c.repo)


@pytest.mark.parametrize("field,value,reason", [
    ("schema", "wrong", "experiment launch or series"),
    ("config_sha256", "wrong", "frozen configuration differs"),
    ("memory", {}, "frozen probe identity"),
    ("training_build", {"build_sha256": "wrong", "tree_hash": "wrong", "manifest_sha256": "wrong"},
     "plan build or model identity"),
])
def test_experiment_record_guards(dense, field, value, reason):
    c = dense
    edit_artifact(c.dense_experiment, "experiment.json", lambda r: r.update({field: value}))
    with pytest.raises(score.ScoreError, match=reason):
        granite.read_experiment(c.dense_experiment, repo=c.repo)


def test_experiment_refuses_wrong_kind(dense):
    c = dense
    manifest_path = c.dense_experiment.with_name(c.dense_experiment.name + ".manifest.json")
    manifest = mf.load_tree(manifest_path)
    manifest.name = "ste-rewriter-r1-e4-plan"
    manifest.save(manifest_path)
    with pytest.raises(score.ScoreError, match="separate Granite experiment"):
        granite.read_experiment(c.dense_experiment, repo=c.repo)


def test_probe_refuses_another_artifact_kind(dense):
    c = dense
    path = c.memory.with_name(c.memory.name + ".manifest.json")
    manifest = mf.load_tree(path)
    manifest.name = "another-probe"
    manifest.save(path)
    with pytest.raises(score.ScoreError, match="separate memory probe artifact"):
        granite.probe_record(c.memory, c.commit, c.loaded.tree_hash, c.repo)


def test_configuration_refuses_other_targets(dense):
    c = dense
    path = c.repo / granite.CONFIG
    path.write_text(path.read_text().replace('"down_proj"', '"in_proj"'))
    with pytest.raises(score.ScoreError, match="approved recipe"):
        granite.configuration(c.repo)


def test_reference_build_must_be_nemotron(dense):
    c = dense
    with pytest.raises(score.ScoreError, match="approved Nemotron pair build"):
        granite.verify_build(c.experiment_record["current_record"],
                             c.experiment_record["current_record"], c.commit, c.repo)


@pytest.mark.parametrize("value", [None, 0, 3, True])
def test_granite_baseline_requires_pass_identity(dense, value):
    c = dense
    with pytest.raises(score.ScoreError, match="untrained Granite score requires pass"):
        score.run_score(c.dense_experiment / "heldout", c.out, procedure=profile.R0,
                        r0_pass=value, repo=c.repo)


def test_granite_score_producer_cannot_reference_other_plan():
    with pytest.raises(score.ScoreError, match="adapter producer used different experiment plans"):
        granite.verify_score_proof({"experiment": "same", "score_plans": {"heldout": ["wrong", "hash"]}},
                                   [None] * 4 + ["tree", "hash"], "same")


def test_granite_review_rejects_training_sample_and_mixed_procedures():
    with pytest.raises(score.ScoreError, match="GRANITE-REVIEW: held-out scores are required"):
        review.granite_sources([{"denominator": 50}], [])
    with pytest.raises(score.ScoreError, match="different procedure in the review batch"):
        review.granite_sources([{"denominator": 100}], [{"record": {"procedure": "r0"}}])


def test_granite_baseline_refuses_reversed_passes(baseline):
    c = baseline
    bundle = score.read_plan(c.dense_experiment / "heldout", repo=c.repo)
    first, second = [report.read_score(path, bundle, repo=c.repo) for path in c.r0]
    with pytest.raises(score.ScoreError, match="ordered distinct Granite baseline passes"):
        dense_report.references(second, first)


@pytest.mark.parametrize("field,value,reason", [
    ("status", "failed", "completed baseline report"),
    ("plan", ["wrong", "hash"], "baseline plan, launch or meaning review"),
])
def test_granite_baseline_report_guard(baseline, field, value, reason):
    c = baseline
    edit_artifact(c.baseline, "report.json", lambda r: r.update({field: value}))
    bundle = score.read_plan(c.dense_experiment / "heldout", repo=c.repo)
    with pytest.raises(score.ScoreError, match=reason):
        dense_report.read_baseline(c.baseline, bundle)


def test_training_rechecks_producer_before_any_score(trained, monkeypatch):
    c = trained
    producer_path = c.procedure / "train/run.json"
    producer = mf.load_run(producer_path)
    producer.commit = {"rev": c.commit["rev"], "dirty": True}
    producer.save(producer_path)
    with pytest.raises(score.ScoreError, match="producer commit differs from clean launch"):
        score.run_score(c.dense_experiment / "heldout", c.out, procedure=profile.R1, epoch=1,
            adapter=c.procedure / "train/adapter-epoch-1", training_build=c.granite_build, repo=c.repo)
    assert len(c.calls) == 2


def test_training_seal_failure_is_failed(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("fixture sealing failure")

    monkeypatch.setattr(e4, "seal", fail)
    with pytest.raises(OSError, match="fixture sealing failure"):
        granite.seal_procedure(tmp_path, {"status": "completed"}, "test")
    record = json.loads((tmp_path / "procedure.json").read_bytes())
    assert record["status"] == "failed"


@pytest.mark.parametrize("change,reason", [
    ("sample", "training sample differs from the fixed Nemotron sequence"),
    ("build", "training input differs from the frozen build"),
    ("producer", "producer default commit differs before launch"),
    ("gpu", "exactly two selected GPUs are required"),
])
def test_pre_launch_guards(baseline, monkeypatch, change, reason):
    import torch

    c = baseline
    if change == "sample":
        monkeypatch.setattr(granite, "TRAINING_SAMPLE", "wrong")
        action = lambda: granite.freeze(c.granite_build, c.build, c.memory, c.folded,
                                         c.out, repo=c.repo)
    else:
        action = lambda: granite.train(c.dense_experiment, c.granite_build,
                                        c.out, baseline=c.baseline, repo=c.repo)
        if change == "build":
            edit_artifact(c.granite_build, "build.json", lambda r: r.update(extra="changed"))
        elif change == "producer":
            original = mf.git_commit
            monkeypatch.setattr(mf, "git_commit", lambda repo=None:
                {"rev": "different", "dirty": False} if repo is None else original(repo))
        else:
            monkeypatch.setattr(torch.cuda, "device_count", lambda: 1)
    with pytest.raises(ValueError, match=reason):
        action()


@pytest.mark.parametrize("field,value,reason", [
    ("review_sample", ["wrong"], "training sample, prompt or review selection"),
    ("sampler", {"fixture": "changed"}, "training and held-out sampler differ"),
])
def test_cross_plan_guards(dense, field, value, reason):
    c = dense
    edit_artifact(c.dense_experiment / "training", "plan.json", lambda r: r.update({field: value}))
    manifest_path = c.dense_experiment.with_name(c.dense_experiment.name + ".manifest.json")
    mf.build_tree(c.dense_experiment, "evaluation", "ste-rewriter-granite-plan").save(manifest_path)
    with pytest.raises(ValueError, match=reason):
        granite.read_experiment(c.dense_experiment, repo=c.repo)


def test_score_plan_guard(dense):
    c = dense
    bundle = list(score.read_plan(c.dense_experiment / "heldout", repo=c.repo))
    bundle[-1] = "wrong"
    with pytest.raises(ValueError, match="not a frozen experiment member"):
        granite.score_plan(c.dense_experiment / "heldout", bundle, c.repo)


def test_producer_artifact_kind_guard(trained):
    c = trained
    path = c.procedure.with_name(c.procedure.name + ".manifest.json")
    manifest = mf.load_tree(path)
    manifest.name = "ste-rewriter-r1-e4-train"
    manifest.save(path)
    producer = mf.load_run(c.procedure / "train/run.json")
    plan = score.read_plan(c.dense_experiment / "heldout", repo=c.repo)[0]
    with pytest.raises(ValueError, match="completed Granite training evidence"):
        granite.producer_proof(c.procedure / "train", producer, plan, c.repo)


def test_report_rechecks_sources_and_commit(baseline, monkeypatch):
    c = baseline
    inputs = dense_report.snapshots([c.baseline])
    record = json.loads((c.baseline / "report.json").read_bytes())
    inputs[c.baseline] = ("different", "identity")
    with pytest.raises(ValueError, match="report source artifact changed"):
        dense_report.save_report(c.out, record, "test", inputs, c.repo)
    monkeypatch.setattr(granite.provenance, "clean_commit", lambda repo: {"rev": "other", "dirty": False})
    with pytest.raises(ValueError, match="report launch commit changed"):
        dense_report.save_report(c.out, record, "test", {}, c.repo)


def test_baseline_rejects_other_model_plan(dense, monkeypatch):
    c = dense
    monkeypatch.setattr(dense_report, "snapshots", lambda paths: {})
    monkeypatch.setattr(score, "read_plan", lambda *a, **kw: ({},))
    with pytest.raises(ValueError, match="Granite baseline requires its own token plan"):
        dense_report.baseline(c.plan, c.out, c.out, c.out, c.out, c.out, repo=c.repo)


def test_score_reader_pass_and_model_guards(baseline):
    c = baseline
    bundle = score.read_plan(c.dense_experiment / "heldout", repo=c.repo)
    edit_artifact(c.r0[0], "run.json", lambda r: r.update(r0_pass=3))
    with pytest.raises(ValueError, match="untrained score must identify its pass"):
        report.read_score(c.r0[0], bundle, repo=c.repo)
    edit_artifact(c.r0[0], "run.json", lambda r: r.update(r0_pass=1, procedure="r0"))
    with pytest.raises(ValueError, match="score and plan series differ"):
        report.read_score(c.r0[0], bundle, repo=c.repo)


def test_comparison_refuses_reversed_r0_passes(baseline):
    c = baseline
    with pytest.raises(ValueError, match="references must be pass 1 then pass 2"):
        report.write_comparison(c.dense_experiment / "heldout", c.r0[1], c.r0[0], c.out, repo=c.repo)


@pytest.fixture
def completed_dense(scored):
    c = scored
    batch, verdicts, _ = verdicts_for(c, c.sources, "complete-epochs")
    c.args = SimpleNamespace(experiment=c.dense_experiment, build=c.granite_build, train=c.procedure,
        baseline=c.baseline, r0_first=c.r0[0], r0_second=c.r0[1], review_batch=batch,
        review_result=verdicts, heldout=c.heldout, training=c.training, out=c.out)
    return c


@pytest.mark.parametrize("change,reason", [
    ("first", "final references differ from the completed baseline"),
    ("baseline", "training used a different baseline report"),
    ("count", "four held-out and four training scores"),
    ("epoch", "score sequence does not identify each epoch"),
])
def test_final_report_guards(completed_dense, monkeypatch, change, reason):
    c = completed_dense
    if change == "first":
        edit_artifact(c.baseline, "report.json", lambda r: r["runs"]["r0_first"].update(identity={}))
    elif change == "baseline":
        edit_artifact(c.procedure, "procedure.json", lambda r: r.update(baseline={}))
    elif change == "count":
        c.args.training = c.args.training[:3]
    else:
        original = report.read_score

        def wrong_epoch(path, *args, **kwargs):
            run = original(path, *args, **kwargs)
            if path in c.heldout:
                run["record"]["epoch"] = 4
            return run

        monkeypatch.setattr(report, "read_score", wrong_epoch)
    with pytest.raises(ValueError, match=reason):
        dense_report.write_report(c.args, repo=c.repo)


@pytest.fixture
def probe_world(dense, monkeypatch):
    """GRANITE-MEMORY: simulate model I/O while checking fixed shape and stop records."""
    import contextlib
    import unsloth
    import torch
    from weaver_train import trainer

    c = dense
    c.parameter = torch.nn.Parameter(torch.tensor(1.0))
    c.probe_mode = "ok"

    class Model:
        def parameters(self):
            return [c.parameter]

        def get_input_embeddings(self):
            return SimpleNamespace(weight=SimpleNamespace(shape=(100352, 64), device="cpu"))

        def train(self):
            return self

        def __call__(self, **kwargs):
            if c.probe_mode == "gradient":
                loss = torch.tensor(1.0, requires_grad=True)
            else:
                loss = c.parameter.square() * (float("nan") if c.probe_mode == "loss" else 1)
            return SimpleNamespace(loss=loss)

        def save_pretrained(self, destination):
            destination.mkdir()
            (destination / "adapter_config.json").write_text("{}")
            (destination / "adapter_model.safetensors").write_bytes(b"fixture adapter")

    model = Model()
    monkeypatch.setattr(unsloth.FastLanguageModel, "from_pretrained", lambda **kw: (model, None))
    monkeypatch.setattr(unsloth.FastLanguageModel, "get_peft_model", lambda model, **kw: model)
    inventory = [{"lora_parameters": 140509184}] + [{"lora_parameters": 0} for _ in range(447)]
    monkeypatch.setattr(trainer, "granite_inventory", lambda *a: inventory)
    monkeypatch.setattr(trainer, "check_granite_adapter", lambda *a: 140509184)
    monkeypatch.setattr(trainer, "pin_layer_devices", lambda model: None)
    monkeypatch.setattr(trainer, "component_coverage", lambda model: {})
    monkeypatch.setattr(torch.cuda, "synchronize", lambda device: None)
    monkeypatch.setattr(torch, "autocast", lambda *a, **kw: contextlib.nullcontext())

    def tokens(low, high, shape, **kwargs):
        assert shape == (1, 49152)
        return torch.tensor([[101, 102, 103, 104]])

    monkeypatch.setattr(torch, "randint", tokens)
    monkeypatch.setattr(torch.optim, "AdamW", lambda *a, **kw:
                        SimpleNamespace(step=lambda: None, zero_grad=lambda **kw: None))
    return c


@pytest.mark.parametrize("mode,reason", [("loss", "non-finite probe loss"),
    ("gradient", "missing or non-finite probe gradient"), ("gpu", "exactly two selected GPUs")])
def test_probe_failures_keep_failed_record(probe_world, monkeypatch, mode, reason):
    import torch
    import rewriter_granite_probe as probe

    c = probe_world
    c.probe_mode = mode
    if mode == "gpu":
        monkeypatch.setattr(torch.cuda, "device_count", lambda: 1)
    with pytest.raises(ValueError, match=reason):
        probe.probe(c.out, repo=c.repo)
    result = json.loads((c.out / "procedure.json").read_bytes())
    assert result["status"] == "failed" and reason in result["failure"]["message"]
    assert not c.out.with_name(c.out.name + ".manifest.json").exists()


def test_probe_success_checks_config_again(probe_world, monkeypatch):
    import rewriter_granite_probe as probe

    c = probe_world
    original = granite.configuration
    calls = []

    def changed(repo):
        config, digest = original(repo)
        calls.append(digest)
        return config, digest if len(calls) == 1 else "wrong"

    monkeypatch.setattr(granite, "configuration", changed)
    with pytest.raises(ValueError, match="configuration changed during the probe"):
        probe.probe(c.out, repo=c.repo)


def test_probe_positive_record(probe_world):
    import rewriter_granite_probe as probe

    c = probe_world
    record, manifest = probe.probe(c.out, repo=c.repo)
    assert record["status"] == "completed" and record["sequence_length"] == 49152
    assert record["finite_gradients"] is True and record["steps"][0]["loss"] == 1
    assert set(record["peak_memory_bytes"]) == {"0", "1"}
    assert mf.require_tree(manifest, c.out) == manifest.tree_hash


def test_builder_rejects_unknown_model(command, monkeypatch):
    c = command
    with pytest.raises(ValueError, match="unknown rendering model"):
        score.build.render_pair(c.loaded.tokenizer, {"input": "source", "target": "target"},
                                 "system", model="unknown")
    path = c.repo / "config/models.toml"
    path.write_text(path.read_text() + '\n[models.unknown]\nrepo_id = "fixture"\nrevision = "fixture-revision"\n')
    manifest_path = score.loader.manifest_path_for("unknown")
    manifest = mf.load_tree(manifest_path)
    manifest.name = "unknown"
    manifest.save(manifest_path)
    with pytest.raises(ValueError, match="unknown tokenizer model"):
        score.build.build_tokenizer(score.build.Evidence(c.repo), "unknown")


def test_adapter_producer_rejects_other_build_model(trained, monkeypatch):
    c = trained
    bundle = score.read_plan(c.dense_experiment / "heldout", repo=c.repo)
    adapter, manifest, _ = score.artifact(c.procedure / "train/adapter-epoch-1")
    original = profile.model_for
    monkeypatch.setattr(profile, "model_for", lambda record:
        score.build.MODEL if record.get("schema") == "weaver-train/rewriter-build/v1" else original(record))
    with pytest.raises(ValueError, match="adapter build and score use different models"):
        score.adapter_producer(adapter, manifest, c.granite_build, profile.R1, 1, bundle[0], c.repo)


def test_training_refuses_changed_input_after_train(trained, monkeypatch):
    from weaver_train import trainer

    c = trained
    original = trainer.train

    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        edit_artifact(c.granite_build, "build.json", lambda r: r.update(changed=True))
        return result

    monkeypatch.setattr(trainer, "train", change)
    with pytest.raises(ValueError, match="frozen inputs changed during training"):
        granite.train(c.dense_experiment, c.granite_build, c.out, baseline=c.baseline, repo=c.repo)
