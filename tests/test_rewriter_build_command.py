"""Spec 0.2 section 9: verified build inputs, selected rows and retained failures."""

import copy
import json
import subprocess
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


def test_granite_build_records_full_pairs_at_32768(command, monkeypatch):
    """GRANITE-DATA: a real synthetic build records its model-specific limit."""
    import rewriter_granite_profile as profile
    import rewriter_provenance as provenance
    from tests.test_rewriter_run_command import commit_fixture

    c = command
    assert invoke(c) == 0
    original = (c.out / "train.jsonl").read_bytes()
    previous = json.loads((c.out / "build.json").read_bytes())
    path = loader.manifest_path_for(build.MODEL)
    manifest = mf.load_tree(path)
    manifest.name = profile.MODEL
    manifest.save(path)
    with (c.repo / "config/models.toml").open("a") as stream:
        stream.write(f'\n[models."{profile.MODEL}"]\nrepo_id = "fixture"\nrevision = "fixture-revision"\n')
    commit_fixture(c.repo)
    monkeypatch.setattr(profile, "verify_model_files", lambda *a: None)
    monkeypatch.setattr(profile, "verify_tokenizer", lambda *a: None)
    monkeypatch.setattr(profile, "verify_render", lambda *a: None)
    monkeypatch.setattr(provenance, "capture", lambda *a, **kw: {"commit": mf.git_commit(c.repo)})
    out = c.out.with_name("granite-build")
    record, manifest = build.write_build(c.pairs, c.folded, profile.R1, out, repo=c.repo)
    assert mf.verify_tree(manifest, out) == []
    assert record["status"] == "completed"
    assert record["model"] == profile.MODEL and record["max_seq_length"] == 32768
    assert previous["max_seq_length"] == 49152
    assert record["counts"]["pairs"] == 200
    assert record["selection"] == previous["selection"]
    assert (out / "train.jsonl").read_bytes() == original
    assert [row["epoch"] for row in record["tokens_per_epoch"]] == [1, 2, 3, 4]


def test_four_epoch_build_preserves_r1_training_bytes(command):
    """REWRITE-R1-E4: only procedure metadata and epoch token records change."""
    from tests.test_rewriter_run_command import commit_fixture

    commit_fixture(command.repo)
    assert invoke(command) == 0
    original = (command.out / "train.jsonl").read_bytes()
    old = json.loads((command.out / "build.json").read_bytes())
    out = command.out.with_name("four-epoch-build")
    assert build.main(["build", "--pairs", str(command.pairs), "--folded-prompt",
        str(command.folded), "--selection", "r1-e4", "--out", str(out)], repo=command.repo) == 0
    record = json.loads((out / "build.json").read_bytes())
    assert (out / "train.jsonl").read_bytes() == original
    assert record["selection"] == old["selection"] and record["counts"] == old["counts"]
    assert record["procedure"] == "r1-e4" and record["commit"]["dirty"] is False
    assert record["tokens_per_epoch"] == [dict(epoch=n, **record["counts"]) for n in (1, 2, 3, 4)]


@pytest.mark.parametrize("change", ["unknown", "dirty", "during"])
def test_four_epoch_build_requires_unchanged_clean_checkout(command, capsys, monkeypatch, change):
    """REWRITE-R1-E4: a dirty, absent or changing launch cannot seal a new build."""
    from tests.test_rewriter_run_command import commit_fixture

    if change != "unknown":
        commit_fixture(command.repo)
    if change == "dirty":
        (command.repo / "change.txt").write_text("changed\n")
    if change == "during":
        render = build.render_pair

        def changed(*args, **kwargs):
            value = render(*args, **kwargs)
            (command.repo / "change.txt").write_text("changed\n")
            return value

        monkeypatch.setattr(build, "render_pair", changed)
    assert build.main(["build", "--pairs", str(command.pairs), "--folded-prompt",
        str(command.folded), "--selection", "r1-e4", "--out", str(command.out)], repo=command.repo) == 2
    expected = ("checkout changed during the training build" if change == "during"
                else "a known clean checkout is required for the training build")
    assert expected in capsys.readouterr().err
    assert not command.out.with_name("build.manifest.json").exists()


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


def test_build_command_rejects_duplicate_pair_identity(command, capsys):
    """PAIR-INPUTS: a repeated pair cannot silently replace a selected member."""
    path = command.pairs / "pairs.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[1] = rows[0]
    save(path, rows, rows=True)
    reseal_pairs(command)
    assert invoke(command) == 2
    assert "PAIR-INPUTS: duplicate or unknown pair identity" in capsys.readouterr().err
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


@pytest.mark.parametrize("source", ["folded", "model", "pairs", "evidence", "template"])
def test_build_command_rejects_input_mutation_during_render(
        command, monkeypatch, source):
    """RENDER-OUTPUT: changed source bytes cannot produce a successful manifest."""
    render = build.render_pair

    def changed_input(*args):
        """RENDER-SEED, RENDER-OUTPUT: change a file after its first verification."""
        result = render(*args)
        if source == "template":
            args[0].chat_template += "{# changed #}"
        else:
            path = {"folded": command.folded, "model": command.model / "weights.bin",
                    "pairs": command.pairs / "pairs.jsonl",
                    "evidence": command.repo / "config/rewriter-inputs.toml"}[source]
            path.write_bytes(b"changed")
        return result

    monkeypatch.setattr(build, "render_pair", changed_input)
    assert invoke(command) == 2
    record = json.loads((command.out / "build.json").read_text())
    assert record["status"] == "failed"
    expected = {"folded": "RENDER-SEED: folded file changed during build",
                "model": "tree does not match", "pairs": "tree does not match",
                "evidence": "PAIR-INPUTS: file changed during collection",
                "template": "RENDER-LAYOUT: tokenizer template changed during build"}
    assert expected[source] in record["failure"]["message"]
    assert not command.out.with_name("build.manifest.json").exists()


def test_build_command_refuses_unapproved_selection(command):
    """PAIR-BALANCE: a smaller balanced maximum cannot replace approved R2."""
    with pytest.raises(build.PairError, match="operator decision"):
        build.write_build(command.pairs, command.folded, "r2", command.out,
                           repo=command.repo)
    assert not command.out.exists()


@pytest.mark.parametrize("source", ["model", "pairs"])
def test_build_command_preserves_input_directory(command, source):
    """REWRITE-LIMITS, RENDER-OUTPUT: refuse output within either verified tree."""
    directory = command.model if source == "model" else command.pairs
    command.out = directory / "build"
    before = mf.build_tree(directory, "model", "fixture").tree_hash
    assert invoke(command) == 2
    assert not command.out.exists()
    assert mf.build_tree(directory, "model", "fixture").tree_hash == before


def test_build_command_accepts_symlinked_pair_directory(command):
    """RENDER-OUTPUT: use one resolved pair tree for both manifest verifications."""
    alias = command.pairs.with_name("pair-alias")
    alias.symlink_to(command.pairs, target_is_directory=True)
    command.pairs = alias
    assert invoke(command) == 0
    record = json.loads((command.out / "build.json").read_text())
    assert record["status"] == "completed" and record["counts"]["pairs"] == 200


@pytest.mark.parametrize("change, message", [
    ("uuid", "PAIR-HELDOUT: pair trajectory overlaps a kit"),
    ("target", "PAIR-TARGET: pair target hash differs"),
    ("source", "PAIR-TARGET: pair source hash differs"),
    ("checked", "PAIR-TARGET: pair checks or review limit differ"),
    ("reviewed", "PAIR-TARGET: pair checks or review limit differ"),
    ("overlap", "PAIR-HELDOUT: pair record contains held-out overlap"),
    ("revision", "PAIR-INPUTS: pair dataset revision differs"),
    ("kit", "PAIR-HELDOUT: pair kit identity differs"),
    ("batch", "PAIR-INPUTS: pair source pin differs"),
    ("schema", "RENDER-OUTPUT: pair record schema differs"),
    ("counts", "RENDER-OUTPUT: pair record counts differ"),
    ("kind", "RENDER-OUTPUT: expected a pair dataset manifest"),
    ("name", "RENDER-OUTPUT: expected a pair dataset manifest"),
])
def test_build_command_rejects_resealed_pair_claim(command, capsys, change, message):
    """PAIR-INPUTS, PAIR-HELDOUT, PAIR-TARGET: isolate each reader semantic guard."""
    pair_path = command.pairs / "pairs.jsonl"
    pairs = [json.loads(line) for line in pair_path.read_text().splitlines()]
    path = command.pairs / "pairs-record.json"
    record = json.loads(path.read_text())
    if change == "uuid":
        pairs[0]["uuid"] = "held-out"
    elif change == "target":
        pairs[0]["target_sha256"] = "0" * 64
    elif change == "source":
        pairs[0]["source_sha256"] = "0" * 64
    elif change == "checked":
        pairs[0]["checks"]["ok"] = False
    elif change == "reviewed":
        pairs[0]["meaning_reviewed"] = True
    elif change == "overlap":
        record["heldout_uuid_overlaps"] = 1
    elif change == "revision":
        record["source_revision"] = "another-revision"
    elif change == "kit":
        record["inputs"]["var/ste/kit-100/source.jsonl"]["sha256"] = "0" * 64
    elif change == "batch":
        record["inputs"]["var/ste/batch-200/converted.jsonl"]["sha256"] = "0" * 64
    elif change == "schema":
        record["schema"] = "another-schema"
    elif change == "counts":
        record["pairs"] += 1
    save(pair_path, pairs, rows=True)
    save(path, record)
    reseal_pairs(command)
    manifest_path = command.pairs.with_name("pairs.manifest.json")
    manifest = mf.load_tree(manifest_path)
    if change == "kind":
        manifest.kind = "model"
    elif change == "name":
        manifest.name = "another-name"
    manifest.save(manifest_path)
    assert invoke(command) == 2
    assert message in capsys.readouterr().err
    assert not command.out.exists()


@pytest.mark.parametrize("field", ["repo_id", "revision"])
def test_build_command_rejects_other_model_source(command, capsys, field):
    """REWRITE-LIMITS: valid tree bytes cannot authorize an unconfigured model."""
    path = command.model.with_name("model.manifest.json")
    manifest = mf.load_tree(path)
    manifest.source[field] = "another-source"
    manifest.save(path)
    assert invoke(command) == 2
    assert "REWRITE-LIMITS: model manifest differs" in capsys.readouterr().err
    assert not command.out.exists()


def test_build_command_rejects_changed_ordered_id_pin(command, capsys):
    """PAIR-BALANCE: the approved count alone cannot authorize different IDs."""
    command.pins["r1_ordered_ids"] = "0" * 64
    write_pins(command)
    record = json.loads((command.pairs / "pairs-record.json").read_text())
    assert record["balance"]["selections"]["r1"]["count"] == 200
    assert invoke(command) == 2
    error = capsys.readouterr().err
    assert "PAIR-BALANCE: selection needs an operator decision" in error
    assert not command.out.exists()


def test_build_command_refuses_unignored_repository_output(command, capsys):
    """RENDER-OUTPUT: a fresh directory inside git must be explicitly ignored."""
    subprocess.run(["git", "init", "--quiet", str(command.repo)], check=True)
    command.out = command.repo / "build"
    assert invoke(command) == 2
    assert "RENDER-OUTPUT: output must stay outside git" in capsys.readouterr().err
    assert not command.out.exists()
