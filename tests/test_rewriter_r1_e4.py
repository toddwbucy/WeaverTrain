"""Spec 0.4 section 9: four epochs, frozen diagnostics and retained review evidence."""

import copy
import json
import random
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rewriter_r1_e4 as e4
import rewriter_r1_e4_report as measurements
import rewriter_report as report
import rewriter_review as review
from tests.test_rewriter_run_command import command, commit_fixture, tokenizer, world
from tests.test_rewriter_score import outputs_for, save_rows

score, mf = e4.score, e4.mf


def save_build(path, record):
    """REWRITE-R1-E4: seal synthetic training metadata without a model procedure."""
    path.mkdir(exist_ok=True)
    (path / "build.json").write_bytes(e4.json_bytes(record))
    (path / "train.jsonl").write_bytes(b'{"fixture":true}\n')
    mf.build_tree(path, "dataset", "ste-rewriter-build").save(
        path.with_name(path.name + ".manifest.json"))


@pytest.fixture
def experiment(command):
    """REWRITE-R1-E4: freeze two plans from one approved synthetic 200-pair build."""
    c = command
    (c.repo / e4.CONFIG).write_bytes((e4.REPO / e4.CONFIG).read_bytes())
    ids = []
    for batch in ("200", "200-2"):
        rows = [{"id": f"s{n:03d}", "uuid": f"train-{batch}-{n}", "kind": "narration",
                 "loc": {"msg": n, "field": "content"}, "raw": "  Use `item`.\n"}
                for n in range(100)]
        path = c.repo / f"var/ste/batch-{batch}/source.jsonl"
        save_rows(path, rows)
        c.pins["source_" + batch.replace("-", "_")] = mf.sha256_file(path)
        ids.extend([[batch, row["id"]] for row in rows])
    c.pins["r1_ordered_ids"] = e4.ordered_hash(ids)
    (c.repo / "config/rewriter-inputs.toml").write_text(
        "".join(f'{key} = "{value}"\n' for key, value in c.pins.items()))
    commit_fixture(c.repo)
    original = json.loads((c.build / "build.json").read_bytes())
    original.update(commit=mf.git_commit(c.repo), selection={"ids": ids,
        "ordered_ids_sha256": c.pins["r1_ordered_ids"]})
    original["counts"]["all_tokens"] = 2000
    original["tokens_per_epoch"] = [dict(epoch=n, **original["counts"]) for n in (1, 2)]
    save_build(c.build, original)
    score.freeze_plan(c.build, c.folded, c.plan, repo=c.repo)
    c.new_build = c.build.with_name("new-build")
    current = copy.deepcopy(original)
    current["procedure"] = "r1-e4"
    current["tokens_per_epoch"] = [dict(epoch=n, **current["counts"]) for n in e4.EPOCHS]
    save_build(c.new_build, current)
    c.experiment = c.build.with_name("experiment")
    c.experiment_record, _ = e4.freeze(c.new_build, c.build, c.plan, c.folded,
                                      c.experiment, repo=c.repo)
    return c


def test_four_epoch_plan_freezes_source_only_training_sample(experiment):
    """REWRITE-R1-E4-TRAIN-SCORES: pin seed, order, code maps and source-only tokens."""
    c = experiment
    record, identity = e4.read_experiment(c.experiment, repo=c.repo)
    heldout = score.read_plan(c.experiment / "heldout", repo=c.repo)
    training = score.read_plan(c.experiment / "training", repo=c.repo)
    e4.same_evaluation(heldout, score.read_plan(c.plan, repo=c.repo))
    chosen = random.Random(2034).sample(record["current_record"]["selection"]["ids"], 50)
    assert record["selected_ids"] == training[1]["selected_ids"] == chosen
    assert record["ordered_ids_sha256"] == e4.ordered_hash(chosen)
    assert training[0]["denominator"] == len(training[2]) == 50
    assert len({tuple(item) for item in chosen}) == 50
    for row, entry in zip(training[1]["rows"], training[2]):
        assert row["text"] == "Use [[C1]]." and row["codes"] == ["`item`"]
        assert row["raw"] == "Use `item`." and "target" not in row
        assert entry["id"] == row["id"]
    assert heldout[0]["review_sample"] == score.read_plan(c.plan, repo=c.repo)[0]["review_sample"]
    assert len(identity["tree_hash"]) == 64


@pytest.mark.parametrize("change, reason", [
    (lambda raw: raw.replace("learning_rate = 1e-4", "learning_rate = 2e-4"),
     "configuration differs beyond the epoch count"),
    (lambda raw: raw.replace("num_train_epochs = 4", "num_train_epochs = 3"),
     "configuration differs beyond the epoch count"),
    (lambda raw: raw.replace("r = 16", "r = 32"), "configuration differs beyond the epoch count"),
])
def test_four_epoch_configuration_changes_only_epochs(experiment, change, reason):
    c = experiment
    path = c.repo / e4.CONFIG
    path.write_text(change(path.read_text()))
    with pytest.raises(score.ScoreError, match=reason):
        e4.configuration(c.repo)


@pytest.mark.parametrize("key", [*score.FIELDS, "inputs", "environment_sha256",
    "input_sha256", "folded_prompt", "prompt_provenance", "sampler", "denominator",
    "source_seed", "review_sample_seed", "review_sample", "reviewer_model", "reviewer_prompt_sha256"])
def test_cross_plan_values_cannot_hide_behind_new_commit(experiment, key):
    c = experiment
    current = list(copy.deepcopy(score.read_plan(c.experiment / "heldout", repo=c.repo)))
    old = score.read_plan(c.plan, repo=c.repo)
    current[0]["commit"] = {"rev": "new", "dirty": False}
    current[0][key] = {"changed": True}
    with pytest.raises(score.ScoreError, match=f"evaluation {key} differs"):
        e4.same_evaluation(current, old)


@pytest.mark.parametrize("index, reason", [(1, "source text or code maps differ"),
    (2, "prompt tokens or budgets differ"), (3, "prompt bytes differ")])
def test_cross_plan_compares_exact_derived_inputs(experiment, index, reason):
    c = experiment
    current = list(copy.deepcopy(score.read_plan(c.experiment / "heldout", repo=c.repo)))
    old = score.read_plan(c.plan, repo=c.repo)
    current[index] = "changed"
    with pytest.raises(score.ScoreError, match=reason):
        e4.same_evaluation(current, old)


def test_training_score_missing_empty_and_length_remain_failures(experiment, tmp_path):
    c = experiment
    kit = score.read_plan(c.experiment / "training", repo=c.repo)[1]
    outputs = outputs_for(kit)
    outputs[0].update(output=None, token_ids=[])
    outputs[1].update(output="", token_ids=[])
    outputs[2].update(finish_reason="length")
    summary, checks = score.check_outputs(kit, outputs, tmp_path / "check50", repo=c.repo,
                                          expected_count=50)
    assert summary["denominator"] == len(checks) == 50
    assert summary["passed"] == 47
    assert [row["check"]["ok"] for row in checks[:3]] == [False, False, False]
    assert measurements.score_gap({"denominator": 100, "passed": 38}, summary) == {
        "heldout_passed": 38, "heldout_denominator": 100, "heldout_rate": 0.38,
        "training_passed": 47, "training_denominator": 50, "training_rate": 0.94,
        "gap_percentage_points": 56}


def curve_fixture():
    """REWRITE-R1-E4-REPORT: recorded rates deliberately differ from a recomputed schedule."""
    losses = [{"step": n, "epoch": n / 25, "loss": n / 1000} for n in range(1, 101)]
    logs = [{**row, "learning_rate": (101 - row["step"]) / 100000} for row in losses]
    producer = SimpleNamespace(results={"global_step": 100, "loss_history": losses})
    return producer, logs


def test_loss_curve_joins_by_step_and_uses_recorded_rates():
    producer, logs = curve_fixture()
    random.Random(17).shuffle(logs)
    curve = measurements.loss_curve(producer, logs)
    assert [row["step"] for row in curve["steps"]] == list(range(1, 101))
    assert curve["steps"][-1]["learning_rate"] == 0.00001
    assert [row["steps"] for row in curve["epochs"]] == [25] * 4
    assert [row["mean_loss"] for row in curve["epochs"]] == pytest.approx(
        [0.013, 0.038, 0.063, 0.088])


@pytest.mark.parametrize("field", ["loss", "learning_rate"])
@pytest.mark.parametrize("change, reason", [
    ("missing", "missing {field} steps"), ("duplicate", "duplicate {field} step"),
    ("nonfinite", "absent or non-finite {field}"),
    ("absent", "absent or non-finite {field}"), ("step", "invalid {field} step identity")])
def test_loss_curve_rejects_bad_step_records(field, change, reason):
    producer, logs = curve_fixture()
    rows = producer.results["loss_history"] if field == "loss" else logs
    if change == "missing":
        rows.pop()
    elif change == "duplicate":
        rows.append(rows[-1].copy())
    elif change == "nonfinite":
        rows[0][field] = float("nan")
    elif change == "absent":
        rows[0].pop(field)
    else:
        rows[0]["step"] = True
    with pytest.raises(score.ScoreError, match=reason.format(field=field)):
        measurements.loss_curve(producer, logs)


def test_shared_expert_names_dimensions_and_no_duplicate_parameters():
    from torch import nn

    shared = nn.Module()
    shared.up_proj = nn.Linear(8, 12, bias=False)
    shared.down_proj = nn.Linear(12, 8, bias=False)
    repeated = nn.Module()
    repeated.up_proj = shared.up_proj
    repeated.down_proj = nn.Linear(12, 8, bias=False)
    model = SimpleNamespace(model=SimpleNamespace(layers=[
        SimpleNamespace(mixer=SimpleNamespace(shared_experts=shared)),
        SimpleNamespace(mixer=SimpleNamespace()),
        SimpleNamespace(mixer=SimpleNamespace(shared_experts=repeated))]))
    result = measurements.expert_modules(model, ["q_proj", "down_proj"])
    assert [row["layer"] for row in result["layers"]] == [0, 2]
    assert result["layers"][0]["modules"][0] == {
        "name": "model.layers.0.mixer.shared_experts.up_proj", "in_features": 8,
        "out_features": 12, "weight_shape": [12, 8], "rank": 16,
        "already_targeted": False, "duplicate_weight": False, "added_parameters": 320}
    assert result["layers"][1]["modules"][0]["duplicate_weight"] is True
    assert result["added_parameters"] == 320


def four_runs():
    """REWRITE-R1-E4-REVIEW: distinct epochs share exactly one producer identity."""
    return [{"record": {"procedure": "r1-e4", "epoch": n,
        "adapter": {"producer": {"run_id": "fixture", "run_sha256": "same", "epoch": n}}}}
        for n in e4.EPOCHS]


@pytest.mark.parametrize("change, reason", [
    (lambda runs: runs.pop(), "all four epochs are required"),
    (lambda runs: runs[1]["record"].update(epoch=1), "duplicate epoch"),
    (lambda runs: runs[0]["record"].update(epoch=5), "epoch is outside"),
    (lambda runs: runs[0]["record"].update(procedure="r1"), "different procedure"),
    (lambda runs: runs[0]["record"]["adapter"]["producer"].update(run_sha256="other"),
     "different training producer")])
def test_four_epoch_review_rejects_incomplete_or_mixed_sources(change, reason):
    runs = four_runs()
    change(runs)
    with pytest.raises(score.ScoreError, match=reason):
        review.four_epoch_sources(({"denominator": 100},), runs)


def test_four_epoch_review_accepts_one_result_from_each_epoch():
    review.four_epoch_sources(({"denominator": 100},), four_runs())


@pytest.mark.parametrize("change, reason", [
    ("procedure", "training build procedure differs"),
    ("commit", "training build launch commit differs"),
    ("ids", "approved training IDs or order differ"),
    ("selection", "training selection differs from original R1"),
    ("counts", "training token counts differ from original R1"),
    ("bytes", "training bytes differ from original R1"),
    ("epochs", "four epoch token records are required"),
    ("folded_prompt", "training folded_prompt differs from original R1"),
    ("prompt_provenance", "training prompt_provenance differs from original R1")])
def test_new_build_matches_each_original_training_identity(experiment, change, reason):
    c = experiment
    current, current_id = e4.training_build(c.new_build)
    original, original_id = e4.training_build(c.build)
    if change == "procedure":
        current["procedure"] = "r1"
    elif change == "commit":
        current["commit"] = None
    elif change == "ids":
        current["selection"]["ids"].reverse()
    elif change == "selection":
        current["selection"]["additional"] = True
    elif change == "counts":
        current["counts"]["all_tokens"] += 1
    elif change == "bytes":
        current_id["train_sha256"] = "different"
    elif change == "epochs":
        current["tokens_per_epoch"].pop()
    else:
        current[change] = "different"
    with pytest.raises(score.ScoreError, match=reason):
        e4.verify_build(current, original, current_id, original_id, mf.git_commit(c.repo), c.repo)


def reseal_plan(path, name):
    """REWRITE-R1-E4: test semantic guards after a valid synthetic tree seal."""
    mf.build_tree(path, "evaluation", name).save(path.with_name(path.name + ".manifest.json"))


def edit_json(path, change):
    """REWRITE-R1-E4: change only the named synthetic record field."""
    value = json.loads(path.read_bytes())
    change(value)
    path.write_bytes(e4.json_bytes(value))


@pytest.mark.parametrize("change, reason", [
    ("schema", "plan schema, count or seed differs"),
    ("sequence", "selected source sequence or hash differs"),
    ("input", "masked source inputs differ"),
    ("environment", "frozen evaluation identities differ"),
    ("membership", "prompt membership differs")])
def test_training_plan_rederives_frozen_values(experiment, change, reason):
    c = experiment
    path = c.experiment / "training"
    if change == "schema":
        edit_json(path / "plan.json", lambda value: value.update(source_seed=0))
    elif change == "sequence":
        edit_json(path / "plan.json", lambda value: value["selected_ids"].reverse())
    elif change == "input":
        (path / "input.jsonl").write_bytes(b"[]\n")
    elif change == "environment":
        edit_json(path / "plan.json", lambda value: value.update(environment_sha256="different"))
    else:
        rows = [json.loads(line) for line in (path / "entries.jsonl").read_bytes().splitlines()]
        rows.reverse()
        (path / "entries.jsonl").write_bytes(score.jsonl_bytes(rows))
    reseal_plan(path, "ste-rewriter-training-plan")
    with pytest.raises(score.ScoreError, match=reason):
        score.read_plan(path, repo=c.repo)


@pytest.mark.parametrize("change, reason", [
    ("kind", "expected a frozen four-epoch experiment"),
    ("commit", "experiment launch commit differs"),
    ("config", "frozen configuration hash differs"),
    ("reference", "frozen reference identity differs"),
    ("build", "score plan training identity differs"),
    ("sample", "frozen training sample identity differs"),
    ("sampler", "sampler differs from held-out plan"),
    ("review", "prompt bytes or review selection differ")])
def test_experiment_binds_both_plans_and_training_record(experiment, change, reason):
    c = experiment
    path = c.experiment
    if change in ("commit", "config", "reference", "sample"):
        field = {"commit": "commit", "config": "config_sha256",
                 "reference": "reference_plan_tree", "sample": "sample_seed"}[change]
        edit_json(path / "experiment.json", lambda value: value.update({field: "different"}))
    elif change == "build":
        edit_json(path / "heldout/plan.json", lambda value: value.update(build_tree_hash="different"))
        reseal_plan(path / "heldout", "ste-rewriter-plan")
    elif change in ("sampler", "review"):
        mutate = (lambda value: value["sampler"].update(num_beams=2)) if change == "sampler" else (
            lambda value: value.update(review_sample=["different"]))
        edit_json(path / "training/plan.json", mutate)
        reseal_plan(path / "training", "ste-rewriter-training-plan")
    reseal_plan(path, "wrong" if change == "kind" else "ste-rewriter-r1-e4-plan")
    with pytest.raises(score.ScoreError, match=reason):
        e4.read_experiment(path, repo=c.repo)


@pytest.mark.parametrize("change, reason", [
    ("steps", "expected 100 optimizer steps"),
    ("epoch", "step epoch identity differs"),
    ("loss", "loss records disagree by step")])
def test_loss_curve_rejects_other_step_join_errors(change, reason):
    producer, logs = curve_fixture()
    if change == "steps":
        producer.results["global_step"] = 99
    elif change == "epoch":
        logs[0]["epoch"] = 0.5
    else:
        logs[0]["loss"] += 1
    with pytest.raises(score.ScoreError, match=reason):
        measurements.loss_curve(producer, logs)


def test_gap_rejects_different_denominators():
    with pytest.raises(score.ScoreError, match="score gap denominators differ"):
        measurements.score_gap({"denominator": 50, "passed": 20}, {"denominator": 50, "passed": 30})


@pytest.mark.parametrize("empty", [False, True])
def test_inventory_requires_shared_expert_linear_modules(empty):
    from torch import nn

    mixer = SimpleNamespace() if not empty else SimpleNamespace(shared_experts=nn.Module())
    model = SimpleNamespace(model=SimpleNamespace(layers=[SimpleNamespace(mixer=mixer)]))
    reason = "shared expert has no linear modules" if empty else "no shared experts were found"
    with pytest.raises(score.ScoreError, match=reason):
        measurements.expert_modules(model, [])


def test_meaning_review_does_not_accept_training_diagnostic():
    with pytest.raises(score.ScoreError, match="held-out scores are required"):
        review.four_epoch_sources(({"denominator": 50},), four_runs())


@pytest.fixture
def produced(experiment, monkeypatch, request):
    """REWRITE-R1-E4: exercise the wrapper and score commands with synthetic model I/O."""
    from weaver_train import trainer
    from peft import PeftModel

    c = experiment
    commit = mf.git_commit(c.repo)
    original_commit = mf.git_commit
    monkeypatch.setattr(mf, "git_commit", lambda repo=None:
                        commit if repo is None else original_commit(repo))
    snapshot = {"commit": commit, "modules": {}, "weaver_train_file": "synthetic"}
    monkeypatch.setattr(e4.provenance, "capture", lambda repo, **kwargs: snapshot)
    monkeypatch.setattr(e4.provenance, "verify_pair", lambda *args: None)
    monkeypatch.setattr(PeftModel, "from_pretrained", lambda model, *args, **kwargs: model)
    monkeypatch.setattr(score.loader, "pin_layer_devices", lambda model: 0)

    def training(model, manifest, dataset, config, out):
        """REWRITE-R1-E4: retain the unchanged trainer's record format, without training."""
        out.mkdir()
        adapters = []
        for epoch in e4.EPOCHS:
            adapter = out / f"adapter-epoch-{epoch}"
            adapter.mkdir()
            (adapter / "adapter_config.json").write_bytes(b"{}\n")
            (adapter / "adapter_model.safetensors").write_bytes(f"fixture epoch {epoch}".encode())
            tree = mf.build_tree(adapter, "model", "fixture-adapter")
            tree.save(adapter.with_name(adapter.name + ".manifest.json"))
            adapters.append({"epoch": epoch, "tree_hash": tree.tree_hash})
        curve, logs = curve_fixture()
        producer = mf.RunRecord(kind="train", seed=0, status="completed", commit=commit,
            model={"name": score.build.MODEL, "tree_hash": c.loaded.tree_hash},
            dataset={"sha256": mf.sha256_file(dataset),
                "build_sha256": mf.sha256_file(dataset.parent / "build.json"),
                "rows": 200, "assistant_tokens": 400}, config=e4.configuration(c.repo)[0],
            results={"epoch_adapters": adapters, **curve.results, "epochs": 4})
        producer.save(out / "run.json")
        (out / "trainer").mkdir()
        (out / "trainer/log_history.json").write_bytes(e4.json_bytes(logs))
        return producer

    monkeypatch.setattr(trainer, "train", training)
    c.procedure = c.build.with_name("four-epoch-train")
    c.training_record, _ = e4.train(c.experiment, c.new_build, c.procedure, repo=c.repo)
    c.sources, c.heldout, c.training = [], [], []
    if getattr(request, "param", None) == "train_only":
        return c
    for epoch in e4.EPOCHS:
        adapter = c.procedure / f"train/adapter-epoch-{epoch}"
        for sample, destination in (("heldout", c.heldout), ("training", c.training)):
            out = c.build.with_name(f"{sample}-{epoch}")
            score.run_score(c.experiment / sample, out, procedure="r1-e4", epoch=epoch,
                            adapter=adapter, training_build=c.new_build, repo=c.repo)
            destination.append(out)
        c.sources.append((c.heldout[-1], adapter, c.new_build))
    return c


def test_four_epochs_retain_distinct_100_and_50_score_records(produced):
    c = produced
    identities = set()
    for epoch in e4.EPOCHS:
        for sample, paths, count in (("heldout", c.heldout, 100), ("training", c.training, 50)):
            run = report.read_score(paths[epoch - 1], score.read_plan(c.experiment / sample,
                repo=c.repo), adapter=c.procedure / f"train/adapter-epoch-{epoch}",
                training_build=c.new_build, repo=c.repo)
            record = run["record"]
            assert record["epoch"] == epoch and record["procedure"] == "r1-e4"
            assert record["experiment"]["sample"] == sample
            assert run["summary"]["denominator"] == len(run["checks"]) == count
            identities.add(record["run_id"])
    assert len(identities) == 8
    assert c.training_record["status"] == "completed"
    assert len(c.training_record["loss_curve"]["steps"]) == 100


def test_four_epoch_prepare_and_ingest_keep_blind_sequence_and_all_hashes(produced):
    c = produced
    batch = c.build.with_name("four-review-batch")
    record, _ = review.prepare(c.experiment / "heldout", c.sources, batch, repo=c.repo)
    audit = json.loads((batch / "private.json").read_bytes())
    blind = [json.loads(line) for line in (batch / "reviewer/input.jsonl").read_bytes().splitlines()]
    assert len(blind) == record["available"] == 120
    assert all(set(item) == {"review_id", "source", "rewrite"} for item in blind)
    assert {item["epoch"] for item in audit["sequence"]} == {1, 2, 3, 4}
    ordered = sorted(audit["sequence"], key=lambda item:
                     (item["procedure"], item["epoch"], item["source_id"]))
    random.Random(2032).shuffle(ordered)
    assert [item["review_id"] for item in ordered] == [item["review_id"] for item in blind]
    external = batch.with_name("four-verdicts.jsonl")
    external.write_bytes(score.jsonl_bytes([{"review_id": item["review_id"],
        "verdict": "faithful", "defects": []} for item in blind]))
    result = batch.with_name("four-review-result")
    args = ["ingest", "--plan", str(c.experiment / "heldout"), "--batch", str(batch),
            "--verdicts", str(external), "--out", str(result),
            "--input-sha256", audit["input_sha256"],
            "--prompt-sha256", audit["prompt_sha256"],
            "--verdicts-sha256", mf.sha256_file(external)]
    for source in c.sources:
        args.extend(["--candidate", *map(str, source)])
    assert review.main(args, repo=c.repo) == 0
    value = json.loads((result / "review.json").read_bytes())
    assert len(value["by_run"]) == 4
    assert all(row["reviewed"] == 30 and row["verdicts"]["faithful"] == 30
               for row in value["by_run"].values())
    assert measurements.meaning_result(result, batch, c.experiment / "heldout",
                                       c.sources, c.repo)[0] == value


@pytest.fixture
def completed(produced):
    """REWRITE-R1-E4-REPORT: complete synthetic old references and the new meaning record."""
    from torch import nn

    c = produced
    first, second = [c.build.with_name(name) for name in ("old-r0-first", "old-r0-second")]
    for out in (first, second):
        score.run_score(c.plan, out, procedure="r0", repo=c.repo)
    old_adapter = c.build.with_name("old-train") / "adapter-epoch-2"
    old_adapter.mkdir(parents=True)
    (old_adapter / "adapter_config.json").write_bytes(b"{}\n")
    (old_adapter / "adapter_model.safetensors").write_bytes(b"original R1 fixture")
    old_tree = mf.build_tree(old_adapter, "model", "original-fixture-adapter")
    old_tree.save(old_adapter.with_name(old_adapter.name + ".manifest.json"))
    original = mf.RunRecord(kind="train", status="completed", seed=0,
        commit={"rev": "f" * 40, "dirty": True},
        model={"name": score.build.MODEL, "tree_hash": c.loaded.tree_hash},
        dataset={"sha256": mf.sha256_file(c.build / "train.jsonl"),
            "build_sha256": mf.sha256_file(c.build / "build.json"),
            "rows": 200, "assistant_tokens": 400},
        config=tomllib.loads((c.repo / "config/rewriter-train.toml").read_text()),
        results={"epoch_adapters": [{"epoch": 2, "tree_hash": old_tree.tree_hash}]})
    original.save(old_adapter.parent / "run.json")
    r1_score = c.build.with_name("old-r1-score")
    score.run_score(c.plan, r1_score, procedure="r1", epoch=2, adapter=old_adapter,
                    training_build=c.build, repo=c.repo)
    batch = c.build.with_name("complete-review-batch")
    review.prepare(c.experiment / "heldout", c.sources, batch, repo=c.repo)
    audit = json.loads((batch / "private.json").read_bytes())
    verdicts = []
    for index, item in enumerate(audit["sequence"]):
        verdict = review.VERDICTS[index % 4]
        defects = [] if verdict == "faithful" else [{"source_quote": "Use",
            "rewrite_quote": "Use", "reason": "Synthetic grader defect."}]
        verdicts.append({"review_id": item["review_id"], "verdict": verdict, "defects": defects})
    external = batch.with_name("complete-verdicts.jsonl")
    external.write_bytes(score.jsonl_bytes(verdicts))
    result = batch.with_name("complete-review-result")
    review.ingest(batch, c.experiment / "heldout", c.sources, external, result,
        input_sha256=audit["input_sha256"], prompt_sha256=audit["prompt_sha256"],
        verdicts_sha256=mf.sha256_file(external), repo=c.repo)
    expert = nn.Module()
    expert.up_proj, expert.down_proj = nn.Linear(8, 12), nn.Linear(12, 8)
    model = SimpleNamespace(model=SimpleNamespace(layers=[
        SimpleNamespace(mixer=SimpleNamespace(shared_experts=expert))]))
    inventory = measurements.expert_modules(model, ["q_proj", "in_proj"])
    inventory.update(model_tree_hash=c.loaded.tree_hash, commit=mf.git_commit(c.repo),
                     training_config_sha256=e4.configuration(c.repo)[1])
    inventory_dir = c.build.with_name("complete-inventory")
    inventory_dir.mkdir()
    (inventory_dir / "inventory.json").write_bytes(e4.json_bytes(inventory))
    e4.seal(inventory_dir, "ste-rewriter-r1-e4-experts")
    c.args = SimpleNamespace(experiment=c.experiment, build=c.new_build, train=c.procedure,
        r0_first=first, r0_second=second, r1_score=r1_score, r1_adapter=old_adapter,
        r1_build=c.build, review_batch=batch, review_result=result, inventory=inventory_dir,
        heldout=c.heldout, training=c.training, out=c.build.with_name("complete-report"))
    return c


def test_final_report_uses_both_references_meaning_and_exact_loss_records(completed):
    c = completed
    record, manifest = measurements.write_report(c.args, repo=c.repo)
    assert record["status"] == "completed" and len(record["epochs"]) == 4
    assert record["loss_curve"] == c.training_record["loss_curve"]
    assert len((c.args.out / "loss.csv").read_text().splitlines()) == 101
    for epoch in record["epochs"]:
        assert epoch["paired"]["r0_first"]["pairs"] == 100
        assert epoch["paired"]["r1_epoch_2"]["pairs"] == 100
        assert epoch["paired"]["r0_first"]["decode_difference"] == (
            epoch["paired"]["r1_epoch_2"]["decode_difference"])
        assert epoch["meaning"]["reviewed"] == 30
        assert sum(epoch["meaning"]["verdicts"].values()) == 30
        assert set(epoch["meaning"]["verdicts"]) == set(review.VERDICTS)
    text = (c.args.out / "report.md").read_text()
    assert text.isascii() and str(c.repo) not in text and "synthetic" not in text
    assert "Epoch | Held-out | Training sample" in text
    assert "Recorded learning rate" in text and "Shared expert module" in text
    mf.require_tree(manifest, c.args.out)
    original = json.loads((c.args.r1_adapter.parent / "run.json").read_bytes())
    assert original["commit"] == {"rev": "f" * 40, "dirty": True}


def test_shared_expert_inventory_uses_meta_without_training(experiment, tiny_model, monkeypatch):
    c = experiment
    model, manifest_path = tiny_model
    commit = mf.git_commit(c.repo)
    state = {"commit": commit, "modules": {}, "weaver_train_file": "synthetic"}
    monkeypatch.setattr(e4.provenance, "capture", lambda *args, **kwargs: state)
    monkeypatch.setattr(score.loader, "manifest_path_for", lambda *args: manifest_path)
    monkeypatch.setattr(score.loader, "resolve_dir", lambda *args: model)
    out = c.build.with_name("meta-inventory")
    record, _ = measurements.inventory(out, repo=c.repo)
    assert record["device"] == "meta"
    assert [layer["layer"] for layer in record["layers"]] == [1]
    assert [module["name"] for module in record["layers"][0]["modules"]] == [
        "model.layers.1.mixer.shared_experts.up_proj",
        "model.layers.1.mixer.shared_experts.down_proj"]
    assert record["added_parameters"] == 4096
    assert record["model_config_sha256"] == mf.sha256_file(model / "config.json")
    assert record["implementation_sha256"]
    mf.require_tree(mf.load_tree(manifest_path), model)


@pytest.mark.parametrize("produced", ["train_only"], indirect=True)
@pytest.mark.parametrize("change, reason", [
    ("kind", "completed training procedure evidence is required"),
    ("status", "training procedure record differs from clean launch"),
    ("producer", "training producer changed after completion"),
    ("config", "training procedure config or build differs"),
    ("epochs", "training procedure epoch identities differ"),
    ("steps", "producer does not record 100 optimizer steps")])
def test_producer_proof_checks_retained_launch_evidence(produced, change, reason):
    c = produced
    root = c.procedure
    train_dir = root / "train"
    record = json.loads((root / "procedure.json").read_bytes())
    producer = mf.RunRecord(**json.loads((train_dir / "run.json").read_bytes()))
    if change == "status":
        record["status"] = "failed"
    elif change == "producer":
        record["producer_sha256"] = "different"
    elif change == "config":
        record["config_sha256"] = "different"
    elif change == "epochs":
        record["epoch_adapters"].reverse()
    elif change == "steps":
        producer.results["global_step"] = 99
        producer.save(train_dir / "run.json")
        record["producer_sha256"] = mf.sha256_file(train_dir / "run.json")
    (root / "procedure.json").write_bytes(e4.json_bytes(record))
    reseal_plan(root, "wrong" if change == "kind" else "ste-rewriter-r1-e4-train")
    with pytest.raises(score.ScoreError, match=reason):
        e4.producer_proof(train_dir, producer, score.read_plan(c.experiment / "heldout",
                                                             repo=c.repo)[0], c.repo)


@pytest.mark.parametrize("produced", ["train_only"], indirect=True)
@pytest.mark.parametrize("change, reason", [
    ("absent", "producer must retain four distinct epoch adapters"),
    ("duplicate", "producer must retain four distinct epoch adapters"),
    ("tree", "saved epoch adapter differs from producer")])
def test_epoch_adapter_verification_covers_all_four(produced, change, reason):
    c = produced
    train_dir = c.procedure / "train"
    producer = mf.RunRecord(**json.loads((train_dir / "run.json").read_bytes()))
    if change == "absent":
        producer.results["epoch_adapters"].pop()
    elif change == "duplicate":
        producer.results["epoch_adapters"][1]["epoch"] = 1
    else:
        producer.results["epoch_adapters"][0]["tree_hash"] = "different"
    with pytest.raises(score.ScoreError, match=reason):
        e4.epoch_adapters(producer, train_dir)


@pytest.mark.parametrize("produced", ["train_only"], indirect=True)
@pytest.mark.parametrize("change, reason", [
    ("data", "training input differs from the frozen experiment"),
    ("commit", "producer default commit differs before training"),
    ("model", "training model differs from the frozen experiment"),
    ("gpus", "exactly two selected GPUs are required for training"),
    ("during", "frozen inputs changed during training")])
def test_training_wrapper_stops_on_launch_or_input_mismatch(produced, monkeypatch, change, reason):
    from weaver_train import trainer
    import torch

    c = produced
    out = c.procedure.with_name("refused-train")
    if change == "data":
        read = e4.training_build
        monkeypatch.setattr(e4, "training_build", lambda path: (read(path)[0], {}))
    elif change == "commit":
        read = mf.git_commit
        monkeypatch.setattr(mf, "git_commit", lambda repo=None: read(repo) if repo else
                            {"rev": "f" * 40, "dirty": False})
    elif change == "model":
        def other(path):
            return SimpleNamespace(tree_hash="different")

        monkeypatch.setattr(mf, "load_tree", other)
    elif change == "gpus":
        monkeypatch.setattr(torch.cuda, "device_count", lambda: 3)
    else:
        train = trainer.train

        def changed(*args, **kwargs):
            producer = train(*args, **kwargs)
            record = json.loads((c.new_build / "build.json").read_bytes())
            record["counts"]["all_tokens"] += 1
            save_build(c.new_build, record)
            return producer

        monkeypatch.setattr(trainer, "train", changed)
    with pytest.raises(score.ScoreError, match=reason):
        e4.train(c.experiment, c.new_build, out, repo=c.repo)
    assert not out.with_name(out.name + ".manifest.json").exists()
    if (out / "procedure.json").exists():
        failure = json.loads((out / "procedure.json").read_bytes())
        assert failure["status"] == "failed" and reason in failure["failure"]["message"]


@pytest.mark.parametrize("produced", ["train_only"], indirect=True)
@pytest.mark.parametrize("when", ["before", "after"])
def test_score_rechecks_producer_commit_before_and_after_generation(produced, monkeypatch, when):
    c = produced
    path = c.procedure / "train/run.json"

    def dirty():
        edit_json(path, lambda value: value["commit"].update(dirty=True))

    if when == "before":
        dirty()
    else:
        decode = score.decode_outputs

        def changed(*args, **kwargs):
            yield from decode(*args, **kwargs)
            dirty()

        monkeypatch.setattr(score, "decode_outputs", changed)
    out = c.build.with_name("refused-score")
    with pytest.raises(score.ScoreError, match="training producer commit differs from clean launch"):
        score.run_score(c.experiment / "heldout", out, procedure="r1-e4", epoch=4,
            adapter=c.procedure / "train/adapter-epoch-4", training_build=c.new_build, repo=c.repo)
    assert not out.with_name(out.name + ".manifest.json").exists()
    record = json.loads((out / "run.json").read_bytes())
    assert record["status"] == "failed"
    assert len(record["completed_ids"]) == (0 if when == "before" else 100)


@pytest.mark.parametrize("change, reason", [
    ("kind", "a completed verdict artifact is required"),
    ("status", "meaning review has not completed"),
    ("counts", "retained verdict counts or identities differ")])
def test_final_meaning_result_is_replayed_before_use(completed, change, reason):
    c = completed
    path = c.args.review_result
    if change == "status":
        edit_json(path / "review.json", lambda value: value.update(status="failed"))
    elif change == "counts":
        edit_json(path / "review.json", lambda value: value["summary"].update(reviewed=119))
    reseal_plan(path, "wrong" if change == "kind" else "ste-rewriter-review-verdicts")
    with pytest.raises(score.ScoreError, match=reason):
        measurements.meaning_result(path, c.args.review_batch, c.experiment / "heldout",
                                     c.sources, c.repo)


@pytest.mark.parametrize("change, reason", [
    ("build", "original R1 build differs from the frozen reference"),
    ("r0", "ordered distinct R0 references are required"),
    ("r1", "original R1 epoch 2 is required"),
    ("experiment", "training uses a different frozen experiment"),
    ("curve", "retained training curve differs"),
    ("epoch", "score epoch or procedure differs"),
    ("inventory-kind", "shared expert inventory artifact is required"),
    ("inventory-id", "inventory reference identities differ"),
    ("source", "source artifact changed during verification"),
    ("commit", "checkout changed during report verification")])
def test_final_report_rejects_mixed_or_changed_evidence(completed, monkeypatch, change, reason):
    c = completed
    if change == "build":
        read = e4.training_build
        monkeypatch.setattr(e4, "training_build", lambda path: (
            (read(path)[0], {}) if path == c.args.r1_build else read(path)))
    elif change in ("r0", "r1", "epoch"):
        read = report.read_score
        changed = []

        def other(path, *args, **kwargs):
            value = read(path, *args, **kwargs)
            if not changed and ((change == "r0" and path == c.args.r0_first) or
                    (change == "r1" and path == c.args.r1_score) or
                    (change == "epoch" and path == c.heldout[0])):
                changed.append(True)
                value["record"]["procedure" if change == "r0" else "epoch"] = "r1" if change == "r0" else 0
            return value

        monkeypatch.setattr(report, "read_score", other)
    elif change == "experiment":
        read = e4.producer_proof
        changed = []

        def other(*args, **kwargs):
            value = read(*args, **kwargs)
            if not changed:
                changed.append(True)
                value["experiment"] = {}
            return value

        monkeypatch.setattr(e4, "producer_proof", other)
    elif change == "curve":
        read = measurements.loss_curve

        def other(*args):
            value = read(*args)
            value["epochs"][0]["mean_loss"] += 1
            return value

        monkeypatch.setattr(measurements, "loss_curve", other)
    elif change.startswith("inventory"):
        if change == "inventory-id":
            edit_json(c.args.inventory / "inventory.json", lambda value:
                      value.update(model_tree_hash="different"))
        reseal_plan(c.args.inventory, "wrong" if change == "inventory-kind" else
                     "ste-rewriter-r1-e4-experts")
    else:
        read = measurements.meaning_result

        def changed(*args, **kwargs):
            value = read(*args, **kwargs)
            if change == "source":
                edit_json(c.args.r0_first / "run.json", lambda value:
                          value.update(extra="changed after verification"))
                reseal_plan(c.args.r0_first, "ste-rewriter-run")
            else:
                (c.repo / "notes.md").write_text("New clean commit after verification.\n")
                commit_fixture(c.repo)
            return value

        monkeypatch.setattr(measurements, "meaning_result", changed)
    with pytest.raises(score.ScoreError, match=reason):
        measurements.write_report(c.args, repo=c.repo)
    assert not c.args.out.exists()


@pytest.mark.parametrize("change, reason", [("kind", "expected a rewriter training build"),
                                           ("status", "training build is not completed")])
def test_four_epoch_build_reader_checks_kind_and_completion(experiment, change, reason):
    c = experiment
    if change == "status":
        edit_json(c.new_build / "build.json", lambda value: value.update(status="failed"))
    mf.build_tree(c.new_build, "dataset", "wrong" if change == "kind" else
                   "ste-rewriter-build").save(c.new_build.with_name("new-build.manifest.json"))
    with pytest.raises(score.ScoreError, match=reason):
        e4.training_build(c.new_build)


def test_training_diagnostic_requires_every_selected_source(experiment):
    c = experiment
    selection = copy.deepcopy(c.experiment_record["current_record"]["selection"])
    selected = random.Random(2034).sample(selection["ids"], 50)[0]
    selection["ids"][selection["ids"].index(selected)] = ["200", "absent"]
    selection["ordered_ids_sha256"] = e4.ordered_hash(selection["ids"])
    c.pins["r1_ordered_ids"] = selection["ordered_ids_sha256"]
    (c.repo / "config/rewriter-inputs.toml").write_text(
        "".join(f'{key} = "{value}"\n' for key, value in c.pins.items()))
    with pytest.raises(score.ScoreError, match="selected source is absent"):
        e4.training_kit(selection, c.repo)


@pytest.mark.parametrize("change, reason", [
    ("reference", "original plan names a different R1 build"),
    ("commit", "checkout changed while freezing both plans")])
def test_freeze_stops_before_sealing_wrong_reference_or_changed_checkout(experiment, monkeypatch, change, reason):
    c = experiment
    if change == "reference":
        edit_json(c.plan / "plan.json", lambda value: value.update(build_sha256="different"))
        reseal_plan(c.plan, "ste-rewriter-plan")
    else:
        read = score.preflight
        calls = []

        def changed(*args, **kwargs):
            value = read(*args, **kwargs)
            calls.append(True)
            if len(calls) == 2:
                (c.repo / "notes.md").write_text("Different clean checkout during preparation.\n")
                commit_fixture(c.repo)
            return value

        monkeypatch.setattr(score, "preflight", changed)
    out = c.build.with_name("refused-experiment")
    with pytest.raises(score.ScoreError, match=reason):
        e4.freeze(c.new_build, c.build, c.plan, c.folded, out, repo=c.repo)
    assert not out.with_name("refused-experiment.manifest.json").exists()


def test_scoring_requires_a_scored_experiment_cohort(experiment):
    c = experiment
    path = c.experiment / "reference"
    with pytest.raises(score.ScoreError, match="score plan is not an experiment cohort"):
        e4.score_plan(path, score.read_plan(path, repo=c.repo), c.repo)


@pytest.mark.parametrize("produced", ["train_only"], indirect=True)
@pytest.mark.parametrize("change", ["experiment", "plan"])
def test_score_checks_pre_training_plan_identity_before_loading(produced, monkeypatch, change):
    c = produced
    read = score.adapter_producer

    def changed(*args, **kwargs):
        proof = read(*args, **kwargs)
        if change == "experiment":
            proof["experiment"]["tree_hash"] = "different"
        else:
            proof["score_plans"]["heldout"][0] = "different"
        return proof

    monkeypatch.setattr(score, "adapter_producer", changed)
    with pytest.raises(score.ScoreError, match="score differs from the pre-training plan"):
        score.run_score(c.experiment / "heldout", c.build.with_name("wrong-plan-score"),
            procedure="r1-e4", epoch=4, adapter=c.procedure / "train/adapter-epoch-4",
            training_build=c.new_build, repo=c.repo)
    assert c.calls == [] and c.decoded == []


@pytest.mark.parametrize("count, reason", [(49, "unsupported input count"),
                                         (100, "expected 100 distinct inputs")])
def test_training_sample_cannot_silently_change_its_denominator(experiment, count, reason):
    c = experiment
    kit = score.read_plan(c.experiment / "training", repo=c.repo)[1]
    if count == 49:
        kit["rows"] = kit["rows"][:49]
    with pytest.raises(score.ScoreError, match=reason):
        score.input_count(kit, count)


def test_outcome_count_rejects_unsupported_denominator():
    ids = [str(n) for n in range(49)]
    checks = [{"id": key, "check": {"ok": True}} for key in ids]
    with pytest.raises(score.ScoreError, match="unsupported outcome count"):
        score.rewrite_outcomes(ids, checks, expected_count=49)


@pytest.mark.parametrize("sample, reason", [("heldout", "experiment plan requires the four-epoch procedure"),
                                          ("training", "training sample requires R1-E4")])
def test_earlier_procedure_cannot_use_four_epoch_plans(experiment, sample, reason):
    c = experiment
    path = c.experiment / sample
    if sample == "training":
        edit_json(path / "plan.json", lambda value: value.pop("procedure"))
        reseal_plan(path, "ste-rewriter-training-plan")
    with pytest.raises(score.ScoreError, match=reason):
        score.run_score(path, c.build.with_name("wrong-procedure"), procedure="r0", repo=c.repo)
    assert c.calls == [] and c.decoded == []


@pytest.mark.parametrize("produced", ["train_only"], indirect=True)
def test_training_seal_failure_preserves_a_failed_record(produced, monkeypatch):
    c = produced

    def refused(*args):
        raise OSError("Synthetic seal failure")

    monkeypatch.setattr(e4, "seal", refused)
    out = c.build.with_name("failed-seal")
    with pytest.raises(OSError, match="Synthetic seal failure"):
        e4.train(c.experiment, c.new_build, out, repo=c.repo)
    record = json.loads((out / "procedure.json").read_bytes())
    assert record["status"] == "failed" and record["failure"]["message"] == "Synthetic seal failure"
    assert not out.with_name(out.name + ".manifest.json").exists()


@pytest.mark.parametrize("produced", ["train_only"], indirect=True)
def test_score_refuses_experiment_identity_change_after_generation(produced, monkeypatch):
    c = produced
    read = e4.score_plan
    calls = []

    def changed(*args):
        value = read(*args)
        calls.append(True)
        if len(calls) == 2:
            value["tree_hash"] = "different"
        return value

    monkeypatch.setattr(e4, "score_plan", changed)
    out = c.build.with_name("changed-experiment-score")
    with pytest.raises(score.ScoreError, match="experiment changed during generation"):
        score.run_score(c.experiment / "heldout", out, procedure="r1-e4", epoch=4,
            adapter=c.procedure / "train/adapter-epoch-4", training_build=c.new_build, repo=c.repo)
    record = json.loads((out / "run.json").read_bytes())
    assert record["status"] == "failed" and len(record["completed_ids"]) == 100
    assert not out.with_name(out.name + ".manifest.json").exists()


@pytest.mark.parametrize("sample", ["heldout", "training"])
def test_score_reader_refuses_old_procedure_on_four_epoch_plan(produced, sample):
    c = produced
    path = (c.heldout if sample == "heldout" else c.training)[0]
    edit_json(path / "run.json", lambda value: value.update(procedure="r0", epoch=0, adapter=None))
    reseal_plan(path, "ste-rewriter-run")
    with pytest.raises(score.ScoreError, match="REWRITE-R1-E4: score procedure differs"):
        report.read_score(path, score.read_plan(c.experiment / sample, repo=c.repo), repo=c.repo)
