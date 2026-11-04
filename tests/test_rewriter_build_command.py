"""Spec 0.2 section 9: verified build inputs, selected rows and retained failures."""

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rewriter_build as build
from tests.test_rewriter_build import save, world, write_pins
from tests.test_rewriter_render import FOLDED, tokenizer
from weaver_train import loader, manifest as mf, trainer


@pytest.fixture
def command(world, tokenizer, tmp_path, monkeypatch):
    """RENDER-OUTPUT: synthetic balanced pairs and a locally saved tokenizer tree."""
    pairs, _, _, record = build.collect(world.repo)
    fixture = pairs[0]
    pairs = []
    for index in range(200):
        size = 12 if index < 74 else 210 if index < 134 else 510
        source = "a" * size
        pairs.append({**copy.deepcopy(fixture), "id": f"T{index:04}.001",
                      "trajectory": f"T{index:04}", "uuid": f"source-{index}",
                      "row": index, "kind": "narration", "input": source,
                      "codes": [], "lead": "", "trail": "", "target": "output",
                      "target_sha256": build.digest(b"output"),
                      "source_sha256": build.digest(source.encode())})
    record.update(pairs=200, repairs=0, excluded=0, balance=build.balance(pairs))
    world.pins.update(folded_prompt=build.digest(FOLDED.encode()))
    for name in ("r1", "r2"):
        world.pins[name + "_ordered_ids"] = record["balance"]["selections"][name][
            "ordered_ids_sha256"]
    write_pins(world)
    pair_dir = tmp_path / "pairs"
    for name, values in (("pairs", pairs), ("repairs", []), ("excluded", [])):
        save(pair_dir / (name + ".jsonl"), values, rows=True)
    save(pair_dir / "pairs-record.json", record)
    mf.build_tree(pair_dir, "dataset", "ste-rewriter-pairs").save(
        pair_dir.with_name("pairs.manifest.json"))
    folded = tmp_path / "folded.txt"
    folded.write_bytes(FOLDED.encode())
    model = tmp_path / "model"
    tokenizer.save_pretrained(model)
    (model / "weights.bin").write_bytes(b"fixture-only model tree member")
    source = {"repo_id": "fixture", "revision": "fixture-revision"}
    model_manifest = mf.build_tree(model, "model", build.MODEL, source=source)
    model_manifest_path = tmp_path / "model.manifest.json"
    model_manifest.save(model_manifest_path)
    (world.repo / "config/models.toml").write_text(
        f'[models."{build.MODEL}"]\nrepo_id = "fixture"\n'
        'revision = "fixture-revision"\n')
    monkeypatch.setattr(loader, "manifest_path_for", lambda name: model_manifest_path)
    monkeypatch.setattr(loader, "resolve_dir", lambda manifest: model)
    return SimpleNamespace(repo=world.repo, pairs=pair_dir, folded=folded,
                           out=tmp_path / "build", model=model, pins=world.pins,
                           model_hash=model_manifest.tree_hash, tokenizer=tokenizer)


def invoke(command):
    """RENDER-OUTPUT: exercise the production CLI with a synthetic local repository."""
    return build.main(["build", "--pairs", str(command.pairs), "--folded-prompt",
                       str(command.folded), "--selection", "r1", "--out",
                       str(command.out)], repo=command.repo)


def reseal_pairs(command):
    """RENDER-OUTPUT: reach semantic checks after a valid fixture manifest."""
    mf.build_tree(command.pairs, "dataset", "ste-rewriter-pairs").save(
        command.pairs.with_name("pairs.manifest.json"))


def test_build_command_without_seed_writes_trainer_compatible_data(command):
    """RENDER-SEED, RENDER-OUTPUT, REWRITE-TRAIN: local bytes suffice for a build."""
    assert invoke(command) == 0
    record = json.loads((command.out / "build.json").read_text())
    rows = [json.loads(line) for line in (command.out / "train.jsonl").read_text(
        ).splitlines()]
    manifest = mf.load_tree(command.out.with_name("build.manifest.json"))
    assert mf.verify_tree(manifest, command.out) == []
    assert {entry.path for entry in manifest.files} == {"build.json", "train.jsonl"}
    assert record["status"] == "completed"
    assert record["tree_hash"] == command.model_hash
    assert record["template_hash"] == build.digest(
        command.tokenizer.get_chat_template().encode())
    assert record["tools"] is False and record["enable_thinking"] is False
    assert record["max_seq_length"] == 49152
    assert record["folded_prompt"] == command.pins["folded_prompt"]
    assert record["prompt_provenance"] == {key: command.pins[key]
                                            for key in build.PROVENANCE}
    assert record["loop_checks"] == {"seed_check_complete": False,
                                     "joint_token_check_complete": False}
    assert record["selection"]["bins"] == [74, 60, 66]
    assert [[row["batch"], row["id"]] for row in rows] == record["selection"]["ids"]
    assert record["completed_ids"] == record["selection"]["ids"]
    assert record["counts"] == {"pairs": 200,
        "all_tokens": sum(len(row["input_ids"]) for row in rows),
        "final_target_tokens": sum(sum(row["assistant_masks"]) for row in rows)}
    assert record["tokens_per_epoch"] == [
        {"epoch": epoch, **record["counts"]} for epoch in (1, 2)]
    expected_tail = command.tokenizer.encode("output<|im_end|>\n",
                                             add_special_tokens=False)
    for row in rows:
        assert row["input_ids"][-len(expected_tail):] == expected_tail
        assert row["assistant_masks"] == (
            [0] * (len(row["input_ids"]) - len(expected_tail))
            + [1] * len(expected_tail))
        assert len(row["initial_messages_sha256"]) == 64
    config = SimpleNamespace(train=SimpleNamespace(max_seq_length=49152))
    checked = trainer.validate_tokenized(command.out / "train.jsonl", config,
                                         command.model_hash, len(command.tokenizer))
    assert checked["rows"] == 200
    assert checked["assistant_tokens"] == record["counts"]["final_target_tokens"]


@pytest.mark.parametrize("source", ["pairs", "manifest", "folded", "model"])
def test_build_command_rejects_changed_bytes_before_output(command, source):
    """RENDER-SEED, RENDER-OUTPUT: valid JSON edits still fail their digests."""
    path = {"pairs": command.pairs / "pairs.jsonl",
            "manifest": command.pairs.with_name("pairs.manifest.json"),
            "folded": command.folded, "model": command.model / "weights.bin"}[source]
    if source == "manifest":
        value = json.loads(path.read_text())
        value["tree_hash"] = "0" * 64
        save(path, value)
    else:
        path.write_bytes(path.read_bytes() + b"\n")
    assert invoke(command) == 2
    assert not command.out.exists()


@pytest.mark.parametrize("field", ["prompt_provenance", "balance", "limits"])
def test_build_command_checks_pair_record_after_manifest(command, field):
    """PAIR-PROVENANCE, PAIR-BALANCE, PAIR-REPAIRS: the sealed record must agree."""
    path = command.pairs / "pairs-record.json"
    record = json.loads(path.read_text())
    if field == "prompt_provenance":
        record[field]["system_text"] = command.pins["folded_prompt"]
    elif field == "balance":
        record[field]["selections"]["r1"]["ids"].reverse()
    else:
        record[field]["repair_training_allowed"] = True
    save(path, record)
    reseal_pairs(command)
    assert invoke(command) == 2
    assert not command.out.exists()


def test_build_command_rejects_duplicate_pair_identity(command):
    """PAIR-INPUTS: a repeated pair cannot silently replace a selected member."""
    path = command.pairs / "pairs.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[1] = rows[0]
    save(path, rows, rows=True)
    reseal_pairs(command)
    assert invoke(command) == 2
    assert not command.out.exists()


@pytest.mark.parametrize("existing", ["directory", "manifest"])
def test_build_command_never_reuses_output(command, existing):
    """RENDER-OUTPUT: even an empty earlier directory is reserved evidence."""
    if existing == "directory":
        command.out.mkdir()
    else:
        command.out.with_name("build.manifest.json").write_text("retained")
    assert invoke(command) == 2
    if existing == "manifest":
        assert command.out.with_name("build.manifest.json").read_text() == "retained"
    else:
        assert list(command.out.iterdir()) == []


def test_build_command_retains_partial_failure_and_refuses_retry(command, monkeypatch):
    """RENDER-LENGTH, REWRITE-RECORD: a failed second row keeps the first row."""
    render = build.render_pair
    calls = 0

    def fail_second(*args):
        """RENDER-LENGTH: inject a failed render after one completed synthetic pair."""
        nonlocal calls
        calls += 1
        if calls == 2:
            raise build.RenderError("RENDER-LENGTH: fixture exceeds 49152")
        return render(*args)

    monkeypatch.setattr(build, "render_pair", fail_second)
    assert invoke(command) == 2
    record = json.loads((command.out / "build.json").read_text())
    assert record["status"] == "failed" and record["counts"]["pairs"] == 1
    assert len(record["completed_ids"]) == 1
    assert "RENDER-LENGTH" in record["failure"]["message"]
    assert len((command.out / "train.jsonl").read_text().splitlines()) == 1
    assert not command.out.with_name("build.manifest.json").exists()
    original = (command.out / "build.json").read_bytes()
    assert invoke(command) == 2
    assert (command.out / "build.json").read_bytes() == original


@pytest.mark.parametrize("source", ["folded", "model"])
def test_build_command_rejects_input_mutation_during_render(
        command, monkeypatch, source):
    """RENDER-OUTPUT: changed source bytes cannot produce a successful manifest."""
    render = build.render_pair

    def changed_input(*args):
        """RENDER-SEED, RENDER-OUTPUT: change a file after its first verification."""
        path = command.folded if source == "folded" else command.model / "weights.bin"
        path.write_bytes(b"changed")
        return render(*args)

    monkeypatch.setattr(build, "render_pair", changed_input)
    assert invoke(command) == 2
    record = json.loads((command.out / "build.json").read_text())
    assert record["status"] == "failed"
    expected = "changed during build" if source == "folded" else "tree does not match"
    assert expected in record["failure"]["message"]
    assert not command.out.with_name("build.manifest.json").exists()


def test_build_command_refuses_unapproved_selection(command):
    """PAIR-BALANCE: a smaller balanced maximum cannot replace approved R2."""
    with pytest.raises(build.PairError, match="operator decision"):
        build.write_build(command.pairs, command.folded, "r2", command.out,
                           repo=command.repo)
    assert not command.out.exists()
