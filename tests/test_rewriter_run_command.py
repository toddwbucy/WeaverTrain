"""Spec 0.2 section 9: frozen score plans and durable first-pass run records."""

import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rewriter_score as score
from tests.test_rewriter_render import tokenizer
from tests.test_rewriter_score import outputs_for, save_pins, world
from weaver_train import loader, manifest as mf


@pytest.fixture
def command(world, tokenizer, tmp_path, monkeypatch):
    """REWRITE-KIT, REWRITE-RECORD: local fixture trees and a controlled decode path."""
    import torch

    world.pins["grouped_instruction"] = score.build.digest(b"fixture grouped prompt")
    world.pins["meaning_review_prompt"] = score.build.digest(b"fixture review prompt")
    save_pins(world)
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
    monkeypatch.setattr(loader, "resolve_dir", lambda manifest: model_dir)
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    record = {"schema": "weaver-train/rewriter-build/v1", "status": "completed",
              "render_only": False, "tree_hash": model_manifest.tree_hash,
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

    def load(*args, **kwargs):
        """REWRITE-DECODE: observe verified-loader arguments without loading weights."""
        calls.append((args, kwargs))
        return loaded

    monkeypatch.setattr(loader, "load", load)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 2)
    monkeypatch.setattr(torch.cuda, "reset_peak_memory_stats", lambda device: None)
    monkeypatch.setattr(torch.cuda, "get_device_name",
                        lambda device=None: "Fixture GPU")
    monkeypatch.setattr(torch.cuda, "get_device_capability", lambda device=None: (8, 6))
    monkeypatch.setattr(torch.cuda, "max_memory_allocated", lambda device: 100 + device)
    monkeypatch.setattr(torch.cuda, "max_memory_reserved", lambda device: 200 + device)
    decoded = []

    def decode(loaded, kit, folded, **kwargs):
        """REWRITE-RECORD: deterministic first outputs for persistence tests only."""
        decoded.append(kit["input_sha256"])
        yield from outputs_for(kit)

    monkeypatch.setattr(score, "decode_outputs", decode)
    return SimpleNamespace(repo=world.repo, build=build_dir, folded=folded,
                           model=model_dir, loaded=loaded, calls=calls, decoded=decoded,
                           plan=tmp_path / "plan", out=tmp_path / "run",
                           pins=world.pins)


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


@pytest.mark.parametrize("change", ["tokens", "budget", "tools", "sampler", "sample"])
def test_rewrite_run_rejects_changed_plan_before_generation(command, change):
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
    assert run(command) == 2 and not command.decoded


def test_rewrite_run_refuses_environment_change(command):
    """REWRITE-RECORD: the environment identity remains frozen between procedures."""
    assert make_plan(command) == 0
    (command.repo / "environment.lock").write_text("different environment\n")
    assert run(command) == 2
    assert not command.calls and not command.out.exists()


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


@pytest.mark.parametrize("changed", [False, True])
def test_rewrite_run_verifies_adapter_before_attachment(command, monkeypatch, changed):
    """REWRITE-DECODE: only a verified adapter reaches the shared first-pass path."""
    from peft import PeftModel

    assert make_plan(command) == 0
    adapter = command.out.parent / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text('{}\n')
    (adapter / "adapter_model.safetensors").write_bytes(b"fixture adapter")
    manifest = mf.build_tree(adapter, "model", "fixture-adapter")
    manifest.save(adapter.with_name("adapter.manifest.json"))
    if changed:
        (adapter / "adapter_model.safetensors").write_bytes(b"changed")
    attached = []

    def attach(model, path, **options):
        """REWRITE-DECODE: observe attachment after complete manifest verification."""
        attached.append((path, options))
        return model

    monkeypatch.setattr(PeftModel, "from_pretrained", attach)
    monkeypatch.setattr(loader, "pin_layer_devices", lambda model: 0)
    status = score.main(["run", "--plan", str(command.plan), "--out", str(command.out),
                         "--procedure", "r1", "--epoch", "1",
                         "--adapter", str(adapter)],
                        repo=command.repo)
    assert status == (2 if changed else 0)
    if changed:
        assert not attached and not command.decoded
    else:
        record = json.loads((command.out / "run.json").read_text())
        assert record["adapter"]["tree_hash"] == manifest.tree_hash
        assert record["procedure"] == "r1" and record["epoch"] == 1
        assert attached == [(str(adapter), {"is_trainable": False,
                                            "local_files_only": True})]


def test_rewrite_commands_preserve_reference_tree(command):
    """REWRITE-LIMITS: refuse plan or score outputs inside the reference model."""
    before = mf.build_tree(command.model, "model", "fixture").tree_hash
    command.plan = command.model / "plan"
    assert make_plan(command) == 2
    assert not command.plan.exists()
    assert mf.build_tree(command.model, "model", "fixture").tree_hash == before
