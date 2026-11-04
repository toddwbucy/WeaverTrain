"""Spec 0.2 section 9: frozen score plans and durable first-pass run records."""

import json
import random
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rewriter_score as score
from tests.test_rewriter_render import tokenizer
from tests.test_rewriter_score import outputs_for, save_pins, world
from weaver_train import loader, manifest as mf


def commit_fixture(repo):
    """REWRITE-RECORD: commit only synthetic inputs in the temporary test repo."""
    for args in (("init", "-q"), ("add", "."),
                 ("commit", "-qm", "Fixture inputs")):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", *args],
                       check=True, capture_output=True)


@pytest.fixture
def command(world, tokenizer, tmp_path, monkeypatch):
    """REWRITE-KIT, REWRITE-RECORD: local fixture trees and a controlled decode path."""
    import torch

    world.pins["grouped_instruction"] = score.build.digest(b"fixture grouped prompt")
    world.pins["meaning_review_prompt"] = score.build.digest(b"fixture review prompt")
    world.pins["r1_ordered_ids"] = score.build.digest(b"fixture ordered pairs")
    save_pins(world)
    (world.repo / "config/ste-rewrite-review-prompt.md").write_bytes(
        b"fixture review prompt")
    (world.repo / "config/rewriter-train.toml").write_bytes(
        (score.REPO / "config/rewriter-train.toml").read_bytes())
    for name in ("ste_check", "ste_convert", "ste_experiment", "ste_score",
                 "ste_prompt", "ste_kit_run"):
        path = world.repo / "scripts" / (name + ".py")
        path.parent.mkdir(exist_ok=True)
        path.write_text("# Frozen fixture identity.\n")
    (world.repo / "environment.lock").write_text("fixture environment\n")
    model_dir = tmp_path / "model"
    tokenizer.save_pretrained(model_dir)
    (model_dir / "weights.bin").write_bytes(b"fixture weights")
    source = {"repo_id": "fixture", "revision": "fixture-revision"}
    model_manifest = mf.build_tree(model_dir, "model", score.build.MODEL, source=source)
    model_manifest_path = tmp_path / "model.manifest.json"
    model_manifest.save(model_manifest_path)
    (world.repo / "config/models.toml").write_text(
        f'[models."{score.build.MODEL}"]\nrepo_id = "fixture"\n'
        'revision = "fixture-revision"\n')
    monkeypatch.setattr(loader, "manifest_path_for", lambda name: model_manifest_path)
    monkeypatch.setattr(loader, "resolve_dir",
                        lambda manifest, path=None: Path(path) if path else model_dir)
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    record = {"schema": "weaver-train/rewriter-build/v1", "status": "completed",
              "render_only": False, "procedure": "r1",
              "counts": {"pairs": 200, "final_target_tokens": 400},
              "selection": {"ordered_ids_sha256": world.pins["r1_ordered_ids"]},
              "tree_hash": model_manifest.tree_hash,
              "template_hash": score.build.digest(
                  tokenizer.get_chat_template().encode()),
              "tools": False, "enable_thinking": False, "max_seq_length": 49152,
              "folded_prompt": world.pins["folded_prompt"],
              "prompt_provenance": {key: world.pins[key]
                                    for key in score.build.PROVENANCE}}
    (build_dir / "build.json").write_text(json.dumps(record))
    (build_dir / "train.jsonl").write_text('{"fixture": true}\n')
    mf.build_tree(build_dir, "dataset", "ste-rewriter-build").save(
        build_dir.with_name("build.manifest.json"))
    folded = tmp_path / "folded.txt"
    folded.write_bytes(world.folded)
    model = SimpleNamespace(parameters=lambda: iter([
        SimpleNamespace(device=torch.device("cuda:0")),
        SimpleNamespace(device=torch.device("cuda:1"))]))
    loaded = SimpleNamespace(model=model, tokenizer=tokenizer,
                             tree_hash=model_manifest.tree_hash)
    calls = []
    original_load, original_pin = loader.load, loader.pin_layer_devices

    def load(*args, **kwargs):
        """REWRITE-DECODE: observe verified-loader arguments without loading weights."""
        calls.append((args, kwargs))
        return loaded

    monkeypatch.setattr(loader, "load", load)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 2)
    monkeypatch.setattr(torch.cuda, "reset_peak_memory_stats", lambda device=None: None)
    monkeypatch.setattr(torch.cuda, "get_device_name",
                        lambda device=None: "Fixture GPU")
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda device=None: (8, 6))
    monkeypatch.setattr(torch.cuda, "max_memory_allocated",
                        lambda device=None: 100 + (device or 0))
    monkeypatch.setattr(torch.cuda, "max_memory_reserved",
                        lambda device=None: 200 + (device or 0))
    decoded = []

    def decode(loaded, kit, folded, **kwargs):
        """REWRITE-RECORD: deterministic first outputs for persistence tests only."""
        decoded.append(kit["input_sha256"])
        yield from outputs_for(kit)

    monkeypatch.setattr(score, "decode_outputs", decode)
    commit_fixture(world.repo)
    return SimpleNamespace(repo=world.repo, build=build_dir, folded=folded,
                           model=model_dir, loaded=loaded, calls=calls, decoded=decoded,
                           plan=tmp_path / "plan", out=tmp_path / "run",
                           pins=world.pins, original_load=original_load,
                           original_pin=original_pin)


def make_plan(command):
    """REWRITE-KIT: exercise the production plan command on local synthetic inputs."""
    return score.main(["plan", "--build", str(command.build), "--folded-prompt",
                       str(command.folded), "--out", str(command.plan)],
                      repo=command.repo)


def run(command, *options):
    """REWRITE-DECODE: execute one synthetic R0 through the production command."""
    return score.main(["run", "--plan", str(command.plan), "--out", str(command.out),
                       "--procedure", "r0", *options], repo=command.repo)


def reseal_plan(command):
    """REWRITE-RECORD: reach semantic checks with a valid fixture manifest."""
    mf.build_tree(command.plan, "evaluation", "ste-rewriter-plan").save(
        command.plan.with_name("plan.manifest.json"))


def test_rewrite_plan_freezes_all_inputs_tokens_and_sample(command):
    """REWRITE-KIT, REWRITE-REVIEW: all 100 renders and 30 sample IDs precede R0."""
    assert make_plan(command) == 0
    plan, kit, entries, folded, tree_hash, manifest_sha = score.read_plan(
        command.plan, repo=command.repo)
    ids = [row["id"] for row in kit["rows"]]
    assert len(ids) == len(entries) == 100
    assert [entry["id"] for entry in entries] == ids
    assert plan["review_sample"] == random.Random(2031).sample(sorted(ids), 30)
    assert plan["reviewer_model"] == "claude-opus-5-5"
    assert plan["inputs"]["config/ste-rewrite-review-prompt.md"] == {
        "sha256": command.pins["meaning_review_prompt"],
        "bytes": len(b"fixture review prompt")}
    assert folded == command.folded.read_bytes()
    assert len(tree_hash) == len(manifest_sha) == 64
    assert not command.calls and not command.decoded
    assert entries == score.preflight(command.loaded.tokenizer, kit, folded,
                                       repo=command.repo)
    assert all(entry["max_new_tokens"] == score.reply_budget(row["text"])
               for entry, row in zip(entries, kit["rows"]))
    assert make_plan(command) == 2


def test_rewrite_run_keeps_complete_results_and_model_identity(command):
    """REWRITE-RECORD, REWRITE-CHECK: keep all raw and checked first outputs."""
    assert make_plan(command) == 0 and run(command) == 0
    record = json.loads((command.out / "run.json").read_text())
    rows = [json.loads(line) for line in (command.out / "segments.jsonl").read_text(
        ).splitlines()]
    manifest = mf.load_tree(command.out.with_name("run.manifest.json"))
    assert mf.verify_tree(manifest, command.out) == []
    assert record["status"] == "completed" and record["adapter"] is None
    assert record["engine"] == "python-hf" and record["precision"] == "bf16"
    assert record["completed_ids"] == [row["id"] for row in rows]
    assert len(rows) == record["checks"]["denominator"] == 100
    assert record["checks"]["passed"] == 100
    assert record["checks"]["by_kind"]["narration"] == {"total": 90, "passed": 90}
    assert record["checks"]["by_bin"]["0"] == {"total": 37, "passed": 37}
    assert record["checks"]["failure_rules"] == {}
    assert record["checks"]["empty_outputs"] == 0
    assert len(record["output_lengths"]) == 100
    assert record["peak_memory_bytes"] == {
        "0": {"allocated": 100, "reserved": 200},
        "1": {"allocated": 101, "reserved": 201}}
    assert record["wall_seconds"] >= 0 and record["review"]["status"] == "pending"
    assert command.calls[0][1]["device_map"] == "balanced"
    assert record["segments_sha256"] == mf.sha256_file(command.out / "segments.jsonl")
    original = (command.out / "run.json").read_bytes()
    assert run(command) == 2
    assert (command.out / "run.json").read_bytes() == original


@pytest.mark.parametrize("text", ["", " \t\n "])
def test_rewrite_run_records_empty_first_outputs(command, monkeypatch, text):
    """REWRITE-CHECK, REWRITE-RECORD: each pass keeps its empty-output count."""
    def decode(loaded, kit, folded, **kwargs):
        """REWRITE-CHECK: one synthetic empty response ends with a stop."""
        outputs = outputs_for(kit)
        outputs[0]["output"] = text
        yield from outputs

    monkeypatch.setattr(score, "decode_outputs", decode)
    assert make_plan(command) == 0 and run(command) == 0
    record = json.loads((command.out / "run.json").read_bytes())
    assert record["checks"]["empty_outputs"] == 1
    assert record["checks"]["failure_rules"] == {"missing": 1}
    assert record["checks"]["passed"] == 99
    assert record["checks"]["denominator"] == 100
    raw = json.loads((command.out / "segments.jsonl").read_bytes().splitlines()[0])
    assert raw["output"] == text and raw["finish_reason"] == "stop"


@pytest.mark.parametrize("change, message", [
    ("tokens", "REWRITE-DECODE: frozen prompt tokens or reply budgets differ"),
    ("budget", "REWRITE-DECODE: frozen prompt tokens or reply budgets differ"),
    ("tools", "RENDER-OUTPUT: tools differs"),
    ("sampler", "REWRITE-DECODE: frozen sampler differs"),
    ("sample", "REWRITE-REVIEW: frozen review sample or prompt differs"),
])
def test_rewrite_run_rejects_changed_plan_before_generation(
        command, capsys, change, message):
    """REWRITE-DECODE, REWRITE-REVIEW: re-sealing cannot change frozen behavior."""
    assert make_plan(command) == 0
    if change in ("tokens", "budget"):
        path = command.plan / "entries.jsonl"
        entries = [json.loads(line) for line in path.read_text().splitlines()]
        if change == "tokens":
            entries[-1]["input_ids"].append(1)
        else:
            entries[-1]["max_new_tokens"] += 1
        path.write_bytes(score.jsonl_bytes(entries))
    else:
        path = command.plan / "plan.json"
        plan = json.loads(path.read_text())
        if change == "tools":
            plan["tools"] = 0
        elif change == "sampler":
            plan["sampler"]["do_sample"] = True
        else:
            plan["review_sample"].reverse()
        path.write_text(json.dumps(plan))
    reseal_plan(command)
    assert run(command) == 2
    assert message in capsys.readouterr().err
    assert not command.decoded


def test_rewrite_run_refuses_environment_change(command, capsys):
    """REWRITE-RECORD: the environment identity remains frozen between procedures."""
    assert make_plan(command) == 0
    (command.repo / "environment.lock").write_text("different environment\n")
    assert run(command) == 2
    assert "REWRITE-RECORD: frozen input identities differ" in capsys.readouterr().err
    assert not command.calls and not command.out.exists()


@pytest.mark.parametrize("action", ["plan", "run"])
@pytest.mark.parametrize("change", ["missing", "changed"])
def test_rewrite_commands_require_pinned_review_prompt(
        command, capsys, action, change):
    """REWRITE-REVIEW: the actual prompt bytes must match the approved pin."""
    if action == "run":
        assert make_plan(command) == 0
    path = command.repo / "config/ste-rewrite-review-prompt.md"
    if change == "missing":
        path.unlink()
    else:
        path.write_bytes(b"Changed fixture review prompt.")
    if action == "plan":
        commit_fixture(command.repo)
    capsys.readouterr()
    assert (make_plan(command) if action == "plan" else run(command)) == 2
    error = capsys.readouterr().err.strip()
    if change == "missing":
        assert error == (
            f"{action}: stopped: [Errno 2] No such file or directory: '{path}'")
    else:
        assert error == (f"{action}: stopped: PAIR-INPUTS: sha256 differs for "
                         "config/ste-rewrite-review-prompt.md")
    output = command.plan if action == "plan" else command.out
    assert not output.exists()
    assert not output.with_name(output.name + ".manifest.json").exists()
    assert not command.calls and not command.decoded


@pytest.mark.parametrize("procedure", ["r0", "r1"])
@pytest.mark.parametrize("change", ["unchanged", "revision", "dirty"])
def test_rewrite_run_requires_frozen_checkout_before_load(
        command, trained, capsys, procedure, change):
    """REWRITE-KIT, REWRITE-RECORD: score only the checkout bound to the plan."""
    assert make_plan(command) == 0
    planned = json.loads((command.plan / "plan.json").read_text())["commit"]
    assert planned == mf.git_commit(command.repo) and planned["dirty"] is False
    if change != "unchanged":
        (command.repo / "notes.md").write_text("Fixture documentation change.\n")
        if change == "revision":
            commit_fixture(command.repo)
        assert mf.git_commit(command.repo) != planned
    capsys.readouterr()
    result = run(command) if procedure == "r0" else score_adapter(command, trained)
    if change == "unchanged":
        assert result == 0
        record = json.loads((command.out / "run.json").read_text())
        assert record["commit"] == planned
        assert command.calls and command.decoded
    else:
        assert result == 2
        assert capsys.readouterr().err.strip() == (
            "run: stopped: REWRITE-RECORD: checkout commit differs from frozen plan")
        assert not command.calls and not command.decoded and not trained.attached
        record = json.loads((command.out / "run.json").read_text())
        assert record["status"] == "failed" and record["completed_ids"] == []
        assert record["failure"]["message"] == (
            "REWRITE-RECORD: checkout commit differs from frozen plan")
        assert record["commit"] == mf.git_commit(command.repo)
        assert not command.out.with_name("run.manifest.json").exists()


@pytest.mark.parametrize("change", ["tracked", "untracked", "non-git",
                                    "unknown-clean", "blank-clean", "nonstring-clean"])
def test_rewrite_plan_requires_known_clean_checkout(
        command, monkeypatch, capsys, change):
    """REWRITE-KIT, REWRITE-RECORD: a plan needs a known, clean code identity."""
    if change == "tracked":
        (command.repo / "notes.md").write_text("Fixture documentation.\n")
        commit_fixture(command.repo)
    if change in ("tracked", "untracked"):
        (command.repo / "notes.md").write_text("Changed fixture documentation.\n")
        assert mf.git_commit(command.repo)["dirty"] is True
    elif change == "non-git":
        (command.repo / ".git").rename(command.repo.with_name("unused-git"))
        assert mf.git_commit(command.repo) == {"rev": None, "dirty": None}
    else:
        revision = {"unknown-clean": None, "blank-clean": "",
                    "nonstring-clean": 1}[change]
        monkeypatch.setattr(mf, "git_commit",
                            lambda repo: {"rev": revision, "dirty": False})
    assert make_plan(command) == 2
    assert capsys.readouterr().err.strip() == (
        "plan: stopped: REWRITE-RECORD: a known clean checkout is required "
        "to freeze the plan")
    assert not command.plan.exists()
    assert not command.plan.with_name("plan.manifest.json").exists()
    assert not command.calls and not command.decoded


@pytest.mark.parametrize("change", ["revision", "dirty", "non-git"])
def test_rewrite_plan_rechecks_checkout_before_seal(
        command, monkeypatch, capsys, change):
    """REWRITE-RECORD: the checked code identity must stay fixed through planning."""
    preflight = score.preflight

    def changed_checkout(*args, **kwargs):
        """REWRITE-KIT: change only Git identity after the initial clean check."""
        entries = preflight(*args, **kwargs)
        if change == "non-git":
            (command.repo / ".git").rename(command.repo.with_name("unused-git"))
        else:
            (command.repo / "notes.md").write_text("Fixture documentation change.\n")
            if change == "revision":
                commit_fixture(command.repo)
        return entries

    monkeypatch.setattr(score, "preflight", changed_checkout)
    assert make_plan(command) == 2
    assert capsys.readouterr().err.strip() == (
        "plan: stopped: REWRITE-RECORD: checkout changed while freezing the plan")
    assert not command.plan.exists()
    assert not command.plan.with_name("plan.manifest.json").exists()
    assert not command.calls and not command.decoded


def test_rewrite_run_retains_partial_decode_failure(command, monkeypatch):
    """REWRITE-RECORD: partial outputs survive failure without a successful seal."""
    assert make_plan(command) == 0

    def failing(loaded, kit, folded, **kwargs):
        """REWRITE-RECORD: fail after exactly three retained synthetic outputs."""
        yield from outputs_for(kit)[:3]
        raise RuntimeError("fixture decode failed")

    monkeypatch.setattr(score, "decode_outputs", failing)
    assert run(command) == 2
    record = json.loads((command.out / "run.json").read_text())
    assert record["status"] == "failed" and len(record["completed_ids"]) == 3
    assert record["failure"]["message"] == "fixture decode failed"
    assert len((command.out / "segments.jsonl").read_text().splitlines()) == 3
    assert not command.out.with_name("run.manifest.json").exists()
    assert run(command) == 2


def test_rewrite_run_retains_model_load_failure(command, monkeypatch):
    """REWRITE-LIMITS, REWRITE-RECORD: model failures get a record without fallback."""
    assert make_plan(command) == 0

    def failing(*args, **kwargs):
        """REWRITE-LIMITS: represent a loader failure before generation begins."""
        raise RuntimeError("fixture load failed")

    monkeypatch.setattr(loader, "load", failing)
    assert run(command) == 2
    record = json.loads((command.out / "run.json").read_text())
    assert record["status"] == "failed" and record["completed_ids"] == []
    assert record["failure"]["message"] == "fixture load failed"
    assert not command.decoded


def test_rewrite_run_retains_seal_failure(command, monkeypatch):
    """REWRITE-RECORD: a manifest write failure cannot leave a completed run record."""
    assert make_plan(command) == 0
    build_tree = mf.build_tree

    def failing(root, *args, **kwargs):
        """REWRITE-RECORD: fail only the final run seal after checker output exists."""
        if Path(root) == command.out:
            raise OSError("fixture seal failed")
        return build_tree(root, *args, **kwargs)

    monkeypatch.setattr(mf, "build_tree", failing)
    assert run(command) == 2
    record = json.loads((command.out / "run.json").read_text())
    assert record["status"] == "failed" and len(record["completed_ids"]) == 100
    assert record["failure"]["message"] == "fixture seal failed"


@pytest.fixture
def trained(command, monkeypatch):
    """REWRITE-RECORD: a synthetic record with the unchanged trainer's field layout."""
    from peft import PeftModel

    original_attach = PeftModel.from_pretrained
    adapter = command.out.parent / "training" / "adapter-epoch-1"
    adapter.mkdir(parents=True)
    (adapter / "adapter_config.json").write_text('{}\n')
    (adapter / "adapter_model.safetensors").write_bytes(b"fixture adapter")
    manifest = mf.build_tree(adapter, "model", "fixture-adapter")
    manifest.save(adapter.with_name(adapter.name + ".manifest.json"))
    producer = mf.RunRecord(
        kind="train", seed=0, status="completed",
        model={"name": score.build.MODEL, "tree_hash": command.loaded.tree_hash},
        dataset={"sha256": mf.sha256_file(command.build / "train.jsonl"),
                 "build_sha256": mf.sha256_file(command.build / "build.json"),
                 "rows": 200, "assistant_tokens": 400},
        config=tomllib.loads((command.repo / "config/rewriter-train.toml").read_text()),
        results={"epoch_adapters": [{"epoch": 1, "tree_hash": manifest.tree_hash},
                                    {"epoch": 2, "tree_hash": "second-epoch"}]})
    producer_path = adapter.parent / "run.json"
    producer.save(producer_path)
    attached = []

    def attach(model, path, **options):
        """REWRITE-DECODE: observe attachment after complete manifest verification."""
        attached.append((path, options))
        return model

    monkeypatch.setattr(PeftModel, "from_pretrained", attach)
    monkeypatch.setattr(loader, "pin_layer_devices", lambda model: 0)
    return SimpleNamespace(adapter=adapter, manifest=manifest, producer=producer,
                           producer_path=producer_path, attached=attached,
                           original_attach=original_attach)


def score_adapter(command, trained, *options):
    """REWRITE-DECODE: name both the epoch artifact and the verified training input."""
    return score.main(["run", "--plan", str(command.plan), "--out", str(command.out),
                       "--procedure", "r1", "--epoch", "1",
                       "--adapter", str(trained.adapter),
                       "--training-build", str(command.build), *options],
                      repo=command.repo)


@pytest.mark.parametrize("changed", [False, True])
def test_rewrite_run_verifies_adapter_before_attachment(command, trained, changed):
    """REWRITE-DECODE: only a verified adapter reaches the shared first-pass path."""
    assert make_plan(command) == 0
    if changed:
        (trained.adapter / "adapter_model.safetensors").write_bytes(b"changed")
    status = score_adapter(command, trained)
    assert status == (2 if changed else 0)
    if changed:
        assert not trained.attached and not command.decoded
    else:
        record = json.loads((command.out / "run.json").read_text())
        assert record["adapter"]["tree_hash"] == trained.manifest.tree_hash
        assert record["adapter"]["producer"]["run_sha256"] == mf.sha256_file(
            trained.producer_path)
        assert record["procedure"] == "r1" and record["epoch"] == 1
        assert trained.attached == [(str(trained.adapter), {"is_trainable": False,
                                                            "local_files_only": True})]


@pytest.mark.parametrize("change", ["procedure", "epoch", "model", "data", "config",
                                    "incomplete", "missing"])
def test_rewrite_run_rejects_wrong_adapter_producer(command, trained, change):
    """REWRITE-CONFIG, REWRITE-RECORD: a valid adapter cannot be mislabeled."""
    assert make_plan(command) == 0
    options = []
    if change == "procedure":
        options = ["--procedure", "r2"]
    elif change == "epoch":
        options = ["--epoch", "2"]
    elif change == "model":
        trained.producer.model["tree_hash"] = "another-model"
    elif change == "data":
        trained.producer.dataset["sha256"] = "another-build"
    elif change == "config":
        trained.producer.config["train"]["num_train_epochs"] = 1
    elif change == "incomplete":
        trained.producer.status = "started"
    trained.producer.save(trained.producer_path)
    if change == "missing":
        trained.producer_path.unlink()
    assert score_adapter(command, trained, *options) == 2
    assert not trained.attached and not command.calls and not command.decoded
    record = json.loads((command.out / "run.json").read_text())
    assert record["status"] == "failed" and record["completed_ids"] == []
    assert not command.out.with_name("run.manifest.json").exists()


def test_rewrite_run_rejects_producer_change(command, trained, monkeypatch):
    """REWRITE-RECORD: producer provenance must remain fixed through generation."""
    assert make_plan(command) == 0

    def changed_producer(loaded, kit, folded, **kwargs):
        """REWRITE-RECORD: alter a valid record after attachment."""
        trained.producer.run_id = "changed-run"
        trained.producer.save(trained.producer_path)
        yield from outputs_for(kit)

    monkeypatch.setattr(score, "decode_outputs", changed_producer)
    assert score_adapter(command, trained) == 2
    record = json.loads((command.out / "run.json").read_text())
    assert record["status"] == "failed" and len(record["completed_ids"]) == 100
    assert "producer changed" in record["failure"]["message"]
    assert not command.out.with_name("run.manifest.json").exists()


def test_rewrite_commands_preserve_reference_tree(command):
    """REWRITE-LIMITS: refuse plan or score outputs inside the reference model."""
    before = mf.build_tree(command.model, "model", "fixture").tree_hash
    command.plan = command.model / "plan"
    assert make_plan(command) == 2
    assert not command.plan.exists()
    assert mf.build_tree(command.model, "model", "fixture").tree_hash == before


def test_rewrite_run_rejects_model_change_during_generation(command, monkeypatch):
    """REWRITE-RECORD: a changed model tree cannot receive a successful run seal."""
    assert make_plan(command) == 0

    def changed_model(loaded, kit, folded, **kwargs):
        """REWRITE-LIMITS: alter one fixture file after the model-load boundary."""
        (command.model / "weights.bin").write_bytes(b"changed")
        yield from outputs_for(kit)

    monkeypatch.setattr(score, "decode_outputs", changed_model)
    assert run(command) == 2
    record = json.loads((command.out / "run.json").read_text())
    assert record["status"] == "failed" and len(record["completed_ids"]) == 100
    assert "tree does not match" in record["failure"]["message"]
    assert not command.out.with_name("run.manifest.json").exists()


def test_rewrite_run_rejects_review_prompt_change_during_generation(
        command, monkeypatch, capsys):
    """REWRITE-REVIEW, REWRITE-RECORD: a changed review prompt prevents a seal."""
    assert make_plan(command) == 0

    def changed_prompt(loaded, kit, folded, **kwargs):
        """REWRITE-REVIEW: change prompt bytes after the first pinned read."""
        (command.repo / "config/ste-rewrite-review-prompt.md").write_bytes(
            b"Changed fixture review prompt.")
        yield from outputs_for(kit)

    monkeypatch.setattr(score, "decode_outputs", changed_prompt)
    assert run(command) == 2
    expected = ("PAIR-INPUTS: sha256 differs for "
                "config/ste-rewrite-review-prompt.md")
    assert capsys.readouterr().err.strip() == "run: stopped: " + expected
    record = json.loads((command.out / "run.json").read_text())
    assert record["status"] == "failed" and len(record["completed_ids"]) == 100
    assert record["failure"]["message"] == expected
    assert not command.out.with_name("run.manifest.json").exists()


@pytest.mark.parametrize("change, message", [
    ("kit", "REWRITE-KIT: frozen source records differ"),
    ("input", "REWRITE-KIT: frozen masked inputs differ"),
    ("folded", "RENDER-SEED: folded prompt sha256 differs"),
    ("provenance", "RENDER-SEED: frozen prompt identities differ"),
    ("kind", "REWRITE-RECORD: expected a frozen rewriter plan"),
    ("schema", "REWRITE-KIT: plan schema or count differs"),
    ("denominator", "REWRITE-KIT: plan schema or count differs"),
    ("membership", "REWRITE-KIT: frozen prompt membership differs"),
])
def test_rewrite_run_rejects_resealed_plan(command, capsys, change, message):
    """REWRITE-KIT, RENDER-SEED: isolate frozen-source checks after manifest checks."""
    assert make_plan(command) == 0
    if change == "input":
        path = command.plan / "input.jsonl"
        path.write_bytes(path.read_bytes() + b"\n")
    elif change == "folded":
        (command.plan / "folded.txt").write_bytes(b"Changed fixture prompt.\n")
    elif change == "kit":
        path = command.plan / "kit.json"
        value = json.loads(path.read_text())
        value["rows"][0]["raw"] = "Changed fixture source."
        path.write_text(json.dumps(value))
    elif change == "membership":
        path = command.plan / "entries.jsonl"
        entries = [json.loads(line) for line in path.read_text().splitlines()]
        entries[0]["id"] = "another-id"
        path.write_bytes(score.jsonl_bytes(entries))
    else:
        path = command.plan / "plan.json"
        plan = json.loads(path.read_text())
        if change == "provenance":
            plan["prompt_provenance"]["system_text"] = "0" * 64
        elif change == "schema":
            plan["schema"] = "another-schema"
        elif change == "denominator":
            plan["denominator"] = 99
        path.write_text(json.dumps(plan))
    reseal_plan(command)
    if change == "kind":
        path = command.plan.with_name("plan.manifest.json")
        manifest = mf.load_tree(path)
        manifest.kind = "dataset"
        manifest.save(path)
    assert run(command) == 2
    assert message in capsys.readouterr().err
    assert not command.calls and not command.out.exists()


def test_rewrite_run_rechecks_plan_after_decode(command, monkeypatch, capsys):
    """REWRITE-RECORD: a plan edit during generation cannot receive a success seal."""
    assert make_plan(command) == 0

    def changed_plan(loaded, kit, folded, **kwargs):
        """REWRITE-KIT: re-seal a valid plan with a changed artifact identity."""
        path = command.plan / "plan.json"
        plan = json.loads(path.read_text())
        plan["commit"]["rev"] = "another-commit"
        path.write_text(json.dumps(plan))
        reseal_plan(command)
        yield from outputs_for(kit)

    monkeypatch.setattr(score, "decode_outputs", changed_plan)
    assert run(command) == 2
    assert "REWRITE-RECORD: plan changed during generation" in capsys.readouterr().err
    record = json.loads((command.out / "run.json").read_text())
    assert record["status"] == "failed" and len(record["completed_ids"]) == 100
    assert not command.out.with_name("run.manifest.json").exists()


def change_build(command, change):
    """REWRITE-RECORD: change a synthetic header while retaining valid file hashes."""
    path = command.build / "build.json"
    record = json.loads(path.read_text())
    if change == "failed":
        record["status"] = "failed"
    elif change == "render_only":
        record["render_only"] = True
    elif change == "provenance":
        record["prompt_provenance"]["system_text"] = "0" * 64
    elif change == "procedure":
        record["procedure"] = "r2"
    elif change in ("tree_hash", "template_hash"):
        record[change] = "0" * 64
    elif change in ("tools", "enable_thinking"):
        record[change] = True
    elif change == "max_seq_length":
        record[change] = 1024
    path.write_text(json.dumps(record))
    manifest = mf.build_tree(command.build, "dataset", "ste-rewriter-build")
    if change == "kind":
        manifest.kind = "model"
    elif change == "name":
        manifest.name = "another-name"
    manifest.save(command.build.with_name("build.manifest.json"))


@pytest.mark.parametrize("change", ["failed", "render_only", "kind", "name",
                                    "provenance", *score.FIELDS])
def test_rewrite_plan_rejects_build_claim(command, capsys, change):
    """RENDER-OUTPUT, RENDER-SEED: plan input must be the completed matching build."""
    change_build(command, change)
    expected = ("REWRITE-RECORD: expected a rewriter build manifest"
                if change in ("kind", "name") else
                "REWRITE-RECORD: a completed rewriter training build is required"
                if change in ("failed", "render_only") else
                "RENDER-SEED: build prompt identities differ"
                if change == "provenance" else f"RENDER-OUTPUT: {change} differs")
    assert make_plan(command) == 2
    assert expected in capsys.readouterr().err
    assert not command.plan.exists() and not command.calls


@pytest.mark.parametrize("change", ["repo_id", "revision", "tree"])
def test_rewrite_run_rejects_other_model(command, capsys, change):
    """REWRITE-LIMITS, REWRITE-DECODE: valid trees need the configured identity."""
    assert make_plan(command) == 0
    path = command.model.with_name("model.manifest.json")
    manifest = mf.load_tree(path)
    if change == "tree":
        (command.model / "weights.bin").write_bytes(b"other fixture weights")
        manifest = mf.build_tree(command.model, "model", score.build.MODEL,
                                  source=manifest.source)
    else:
        manifest.source[change] = "another-source"
    manifest.save(path)
    assert run(command) == 2
    expected = ("REWRITE-DECODE: model manifest differs from frozen plan"
                if change == "tree" else
                "REWRITE-LIMITS: model manifest differs from configured reference")
    assert expected in capsys.readouterr().err
    assert not command.calls and not command.out.exists()


@pytest.mark.parametrize("placement", ["cpu", "cuda:0", "one-selected"])
def test_rewrite_run_requires_both_gpus(command, monkeypatch, capsys, placement):
    """REWRITE-LIMITS: reject incomplete selection or placement before generation."""
    import torch

    assert make_plan(command) == 0
    if placement == "one-selected":
        monkeypatch.setattr(torch.cuda, "device_count", lambda: 1)
    else:
        command.loaded.model.parameters = lambda: iter([
            SimpleNamespace(device=torch.device(placement))])
    assert run(command) == 2
    expected = ("REWRITE-LIMITS: exactly two selected GPUs are required"
                if placement == "one-selected" else
                "REWRITE-LIMITS: all parameters must span the two selected GPUs")
    assert expected in capsys.readouterr().err
    assert not command.decoded


@pytest.mark.parametrize("procedure, epoch, adapter, training", [
    ("r0", 0, True, False), ("r0", 0, False, True),
    ("r1", 1, False, False), ("r1", 1, True, False),
    ("r1", 1, False, True), ("r1", 0, True, True), ("r1", 3, True, True),
])
def test_rewrite_run_refuses_bad_arguments(
        command, capsys, procedure, epoch, adapter, training):
    """REWRITE-ORDER: invalid procedure arguments stop before any run output."""
    assert make_plan(command) == 0
    options = ["--procedure", procedure, "--epoch", str(epoch)]
    if adapter:
        options.extend(("--adapter", str(command.out.parent / "adapter")))
    if training:
        options.extend(("--training-build", str(command.build)))
    assert run(command, *options) == 2
    assert "REWRITE-ORDER: adapter scores need an epoch" in capsys.readouterr().err
    assert not command.out.exists() and not command.calls


@pytest.mark.parametrize("change", ["kind", "provenance", "procedure", *score.FIELDS])
def test_rewrite_run_isolates_producer_build_checks(command, trained, capsys, change):
    """REWRITE-RECORD: each producer build guard must reject its own mismatch."""
    assert make_plan(command) == 0
    change_build(command, change)
    trained.producer.dataset["build_sha256"] = mf.sha256_file(
        command.build / "build.json")
    trained.producer.save(trained.producer_path)
    assert score_adapter(command, trained) == 2
    expected = ("REWRITE-RECORD: adapter needs a rewriter training build"
                if change == "kind" else
                "REWRITE-RECORD: adapter training procedure differs"
                if change == "procedure" else
                "RENDER-SEED: adapter training prompt differs"
                if change == "provenance" else f"RENDER-OUTPUT: {change} differs")
    assert expected in capsys.readouterr().err
    assert not trained.attached and not command.decoded


@pytest.mark.parametrize("change", ["kind", "missing-weights"])
def test_rewrite_run_refuses_non_adapter(command, trained, capsys, change):
    """REWRITE-DECODE: a valid artifact manifest must still describe adapter files."""
    assert make_plan(command) == 0
    if change == "missing-weights":
        (trained.adapter / "adapter_model.safetensors").unlink()
    kind = "dataset" if change == "kind" else "model"
    manifest = mf.build_tree(trained.adapter, kind, "fixture-adapter")
    manifest.save(trained.adapter.with_name(trained.adapter.name + ".manifest.json"))
    assert score_adapter(command, trained) == 2
    assert "REWRITE-DECODE: adapter files are missing" in capsys.readouterr().err
    assert not trained.attached and not command.decoded


def test_rewrite_run_refuses_incomplete_decode(command, monkeypatch, capsys):
    """REWRITE-DECODE: a decoder ending early must keep a failed partial record."""
    assert make_plan(command) == 0
    def incomplete(loaded, kit, folded, **kwargs):
        """REWRITE-DECODE: end normally with one planned ID still absent."""
        yield from outputs_for(kit)[:99]

    monkeypatch.setattr(score, "decode_outputs", incomplete)
    assert run(command) == 2
    assert "REWRITE-DECODE: first outputs do not cover" in capsys.readouterr().err
    record = json.loads((command.out / "run.json").read_text())
    assert record["status"] == "failed" and len(record["completed_ids"]) == 99
    assert not command.out.with_name("run.manifest.json").exists()


@pytest.mark.gpu
@pytest.mark.multigpu
def test_rewrite_run_pins_attached_adapter_on_both_gpus(
        command, trained, tiny_model, monkeypatch):
    """REWRITE-LIMITS: the command must pin a newly attached split adapter model."""
    import torch
    from peft import LoraConfig, PeftModel, get_peft_model
    from tests.conftest import SPLIT

    loaded = command.original_load(path=tiny_model[0], manifest_path=tiny_model[1],
                                    device_map=SPLIT)
    assert loaded.pinned_layers == 0
    adapted = get_peft_model(loaded.model, LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.0, task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "in_proj"]))
    adapted.save_pretrained(trained.adapter)
    command.loaded.model = adapted.unload()
    trained.manifest = mf.build_tree(trained.adapter, "model", "fixture-adapter")
    trained.manifest.save(trained.adapter.with_name(
        trained.adapter.name + ".manifest.json"))
    trained.producer.results["epoch_adapters"][0]["tree_hash"] = (
        trained.manifest.tree_hash)
    trained.producer.save(trained.producer_path)
    monkeypatch.setattr(PeftModel, "from_pretrained", trained.original_attach)
    monkeypatch.setattr(loader, "pin_layer_devices", command.original_pin)
    layers = [layer for name, module in command.loaded.model.named_modules()
              if isinstance(module, torch.nn.ModuleList) and name.endswith("layers")
              for layer in module]
    assert layers
    assert all(not hasattr(layer, "_weaver_pinned_device") for layer in layers)
    seen = set()

    def observe_device(module, args):
        """REWRITE-LIMITS: assert the device before any Mamba kernel can run."""
        expected = next(module.parameters()).device.index
        assert torch.cuda.current_device() == expected
        seen.add(expected)

    def decode_after_attachment(loaded, kit, folded, **kwargs):
        """REWRITE-DECODE: exercise command hooks without the decoder helper's pins."""
        handles = [layer.register_forward_pre_hook(observe_device) for layer in layers]
        try:
            torch.cuda.set_device(1)
            with torch.inference_mode():
                logits = loaded.model(torch.ones((1, 16), dtype=torch.long,
                    device="cuda:0"), use_cache=False).logits
            assert torch.isfinite(logits).all()
        finally:
            for handle in handles:
                handle.remove()
        yield from outputs_for(kit)

    monkeypatch.setattr(score, "decode_outputs", decode_after_attachment)
    assert make_plan(command) == 0 and score_adapter(command, trained) == 0
    assert seen == {0, 1}
    assert all(hasattr(layer, "_weaver_pinned_device") for layer in layers)


@pytest.mark.parametrize("source", ["evidence", "model", "review-prompt"])
def test_rewrite_plan_rechecks_inputs_after_preflight(
        command, monkeypatch, capsys, source):
    """REWRITE-RECORD: preflight cannot seal a plan from changed inputs."""
    preflight = score.preflight
    relative = ("config/ste-rewrite-review-prompt.md" if source == "review-prompt"
                else "environment.lock")

    def changed_input(*args, **kwargs):
        """REWRITE-KIT, REWRITE-LIMITS: change a file after initial verification."""
        result = preflight(*args, **kwargs)
        path = (command.model / "weights.bin" if source == "model" else
                command.repo / relative)
        path.write_bytes(b"changed fixture input")
        return result

    monkeypatch.setattr(score, "preflight", changed_input)
    assert make_plan(command) == 2
    expected = ("tree does not match" if source == "model" else
                f"PAIR-INPUTS: file changed during collection: {relative}")
    assert expected in capsys.readouterr().err
    assert not command.plan.exists()
    assert not command.plan.with_name("plan.manifest.json").exists()
