"""GRANITE-DATA, GRANITE-MEMORY: context and placement refusals use valid other inputs."""

import copy
import json
from types import SimpleNamespace

import pytest

import rewriter_build as build
import rewriter_granite as granite
import rewriter_granite_probe as probe
import rewriter_granite_profile as profile
from weaver_train import trainer
from tests.test_granite_training_padding import training_load
from tests.test_rewriter_granite import (
    command, dense, edit_artifact, experiment, placement_fixture, probe_world, tokenizer, world,
)


def test_approved_map_moves_only_layer_32():
    """GRANITE-MEMORY: preserve the failed attempt's other module assignments."""
    previous = {"model.embed_tokens": 0,
        **{f"model.layers.{i}": int(i >= 32) for i in range(64)},
        "model.norm": 1, "model.rotary_emb": 1, "lm_head": 1}
    current = profile.training_device_map()
    assert set(current) == set(previous)
    assert [key for key in previous if previous[key] != current[key]] == ["model.layers.32"]
    assert current["model.layers.32"] == 0
    raw, _ = granite.configuration(granite.REPO)
    assert raw["train"]["device_map"] == current
    assert raw["train"]["max_seq_length"] == 32768


@pytest.mark.parametrize("old,new", [
    ("max_seq_length = 32768", "max_seq_length = 49152"),
    ('"model.layers.32" = 0', '"model.layers.32" = 1'),
    ('"model.layers.33" = 1', '"model.layers.33" = 0'),
])
def test_configuration_refuses_context_or_boundary_change(tmp_path, old, new):
    """GRANITE-MEMORY: reject context or placement drift with valid base recipes."""
    (tmp_path / "config").mkdir()
    for name in (granite.CONFIG, granite.e4.CONFIG, "config/rewriter-train.toml"):
        raw = (granite.REPO / name).read_text()
        (tmp_path / name).write_text(raw.replace(old, new) if name == granite.CONFIG else raw)
    with pytest.raises(ValueError, match="configuration differs from the approved recipe"):
        granite.configuration(tmp_path)


@pytest.mark.parametrize("mapping", [{}, {"model": "cpu"}, {"model": "disk"},
    {"model": True}, {"model": -1}, {"": 0}, {1: 0}])
def test_explicit_map_rejects_invalid_entries(mapping):
    """GRANITE-MEMORY: explicit training placement accepts GPU indices only."""
    raw, _ = granite.configuration(granite.REPO)
    raw["train"]["device_map"] = mapping
    with pytest.raises(trainer.TrainConfigError, match="explicit device map needs module names and nonnegative GPU indices"):
        trainer.parse_config(raw)


@pytest.mark.parametrize("change,reason", [
    ("metadata", "loaded device map differs"), ("missing_map", "loaded device map differs"),
    ("missing_parameters", "parameter placement differs"),
    ("empty_parameters", "parameter placement differs"),
    ("parameter", "parameter placement differs"), ("unknown", "parameter placement differs"),
])
def test_explicit_map_checks_actual_parameters(change, reason):
    """GRANITE-MEMORY: a declared map cannot hide missing or misplaced weights."""
    placement = placement_fixture()
    if change == "metadata":
        placement["hf_device_map"]["model.layers.32"] = "1"
    elif change == "missing_map":
        del placement["hf_device_map"]
    elif change == "missing_parameters":
        del placement["parameter_devices"]
    elif change == "empty_parameters":
        placement["parameter_devices"] = {}
    elif change == "parameter":
        placement["parameter_devices"]["model.layers.32.weight"] = "cuda:1"
    else:
        placement["parameter_devices"]["unknown.weight"] = "cuda:0"
    with pytest.raises(RuntimeError, match=reason):
        trainer.check_granite_device_map(placement, profile.training_device_map())


@pytest.mark.parametrize("phase", ["before", "after"])
def test_probe_refuses_wrong_layer_before_forward(probe_world, monkeypatch, phase):
    """GRANITE-MEMORY: validate actual placement at both probe load boundaries."""
    c = probe_world
    calls = []

    def placement(model):
        """GRANITE-MEMORY: make only the selected probe placement check fail."""
        calls.append(model)
        observed = copy.deepcopy(c.placement)
        if len(calls) == (1 if phase == "before" else 2):
            observed["parameter_devices"]["model.layers.32.weight"] = "cuda:1"
        return observed

    monkeypatch.setattr(granite, "loaded_placement", placement)
    with pytest.raises(RuntimeError, match="parameter placement differs from configured map"):
        probe.probe(c.out, repo=c.repo)
    assert not c.probe_forwards
    record = json.loads((c.out / "procedure.json").read_bytes())
    assert record["status"] == "failed"
    assert record["device_map"] == profile.training_device_map()


@pytest.mark.parametrize("phase", ["before", "after"])
def test_training_refuses_wrong_layer_before_optimizer(training_load, monkeypatch, phase):
    """GRANITE-R1: validate actual placement before and after adapter attachment."""
    c = training_load
    calls = []

    def placement(model):
        """GRANITE-R1: make only the selected training placement check fail."""
        calls.append(model)
        observed = copy.deepcopy(c.placement)
        if len(calls) == (1 if phase == "before" else 2):
            observed["parameter_devices"]["model.layers.32.weight"] = "cuda:1"
        return observed

    if phase == "before":
        c.expected_placement["parameter_devices"]["model.layers.32.weight"] = "cuda:1"
    monkeypatch.setattr(trainer, "granite_placement", placement)
    with pytest.raises(RuntimeError, match="parameter placement differs from configured map"):
        c.launch()
    assert [event for event, _ in c.events] == (["load"] if phase == "before" else ["load", "attach"])
    assert not (c.out / "adapter").exists()


@pytest.mark.parametrize("field", ["device_map", "placement_before", "placement_after"])
@pytest.mark.parametrize("missing", [False, True])
def test_saved_probe_requires_explicit_map(dense, field, missing):
    """GRANITE-MEMORY: reject an invalid recorded map with valid other evidence."""
    c = dense

    def change(record):
        """GRANITE-MEMORY: preserve other checks while changing one map claim."""
        if missing:
            del record[field]
        elif field == "device_map":
            record[field]["model.layers.32"] = 1
            for phase in ("before", "after"):
                placement = record["placement_" + phase]
                placement["hf_device_map"]["model.layers.32"] = "1"
                placement["parameter_devices"]["model.layers.32.weight"] = "cuda:1"
        else:
            record[field]["hf_device_map"]["model.layers.32"] = "1"

    edit_artifact(c.memory, "procedure.json", change)
    reason = "recorded training device map differs" if field == "device_map" else "loaded device map differs"
    with pytest.raises((ValueError, RuntimeError), match=reason):
        granite.probe_record(c.memory, c.commit, c.loaded.tree_hash, c.repo)


@pytest.mark.parametrize("phase", ["freeze", "heldout", "training"])
def test_score_plan_refuses_old_context(dense, phase):
    """GRANITE-DATA: neither a new plan nor a loaded plan accepts the old limit."""
    c, score = dense, granite.score
    if phase == "freeze":
        edit_artifact(c.granite_build, "build.json", lambda row: row.update(max_seq_length=49152))
        with pytest.raises(ValueError, match="RENDER-OUTPUT: max_seq_length differs"):
            score.freeze_plan(c.granite_build, c.folded, c.granite_build.with_name("new-plan"), repo=c.repo)
    else:
        plan, kit, entries, folded = score.read_plan(c.dense_experiment / phase, repo=c.repo)[:4]
        plan["max_seq_length"] = 49152
        with pytest.raises(ValueError, match="RENDER-OUTPUT: max_seq_length differs"):
            score.validate_loaded_plan(plan, kit, entries, folded, c.loaded, repo=c.repo,
                                       expected_count=plan["denominator"])


@pytest.mark.parametrize("model,limit", [(profile.MODEL, 32768), (build.MODEL, 49152)])
@pytest.mark.parametrize("overflow", [0, 1])
def test_render_context_boundary_keeps_intact_sequences(monkeypatch, model, limit, overflow):
    """GRANITE-DATA: accept the full limit and refuse one extra token without cuts."""
    monkeypatch.setattr(profile, "verify_render", lambda *a: None)
    pair = {key: "fixture" for key in ("batch", "id", "trajectory", "uuid", "row", "kind", "loc", "target_round", "input", "target")}

    def apply(messages, **options):
        """GRANITE-DATA: return an intact synthetic sequence at the chosen boundary."""
        assert "truncation" not in options
        return [1] if options["add_generation_prompt"] else [1] * (limit + overflow)

    tok = SimpleNamespace(apply_chat_template=apply)
    if overflow:
        with pytest.raises(build.RenderError, match=f"RENDER-LENGTH:.*limit {limit}"):
            build.render_pair(tok, pair, "rules", model=model)
    else:
        row = build.render_pair(tok, pair, "rules", model=model)
        assert len(row["input_ids"]) == len(row["assistant_masks"]) == limit
        assert sum(row["assistant_masks"]) == limit - 1


@pytest.mark.parametrize("count", [50, 100])
@pytest.mark.parametrize("model,limit", [(profile.MODEL, 32768), (build.MODEL, 49152)])
@pytest.mark.parametrize("overflow", [0, 1])
def test_score_context_boundary_counts_reply_budget(world, monkeypatch, count, model, limit, overflow):
    """GRANITE-DATA: both score plans count the complete prompt and reply budget."""
    score = granite.score
    kit = score.load_kit(world.repo)
    kit["rows"] = kit["rows"][:count]
    monkeypatch.setattr(profile, "verify_render", lambda *a: None)
    calls = []

    def apply(messages, **options):
        """GRANITE-DATA: vary only the final prompt's combined token budget."""
        assert "truncation" not in options
        calls.append(messages)
        return [1] * (limit - score.reply_budget(kit["rows"][-1]["text"]) + overflow) if len(calls) == count else [1]

    tok = SimpleNamespace(apply_chat_template=apply)
    if overflow:
        with pytest.raises(ValueError, match=f"{kit['rows'][-1]['id']} prompt plus reply exceeds {limit}"):
            score.preflight(tok, kit, world.folded, repo=world.repo, model=model, expected_count=count)
    else:
        entries = score.preflight(tok, kit, world.folded, repo=world.repo, model=model, expected_count=count)
        assert len(entries[-1]["input_ids"]) + entries[-1]["max_new_tokens"] == limit
        assert len(entries) == count
    assert len(calls) == count
