"""GRANITE-VERIFY: persist actual R1 IDs and refuse drift before training begins."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import rewriter_granite as granite
from tests.test_rewriter_granite import PADDING_BEFORE, PADDING_AFTER, placement_fixture
from tests.test_trainer import make_tokenized
from weaver_train import manifest as mf, trainer


@pytest.fixture
def training_load(tmp_path, monkeypatch):
    """Use real records and manifests, with synthetic model loading and optimizer I/O."""
    import unsloth
    import torch
    import transformers
    import trl

    c = SimpleNamespace(root=tmp_path / "model", out=tmp_path / "training",
        before=dict(PADDING_BEFORE), after=dict(PADDING_AFTER), events=[], load_error=None,
        placement=placement_fixture(), expected_placement=placement_fixture(), load_options=None)
    c.root.mkdir()
    c.manifest = tmp_path / "model.manifest.json"

    def observe(event):
        c.events.append((event, copy.deepcopy(mf.load_run(c.out / "run.json").results)))

    def config(source):
        return SimpleNamespace(model_type="granite", to_dict=lambda: {
            "pad_token_id": c.after[source + "_pad_token_id"],
            "eos_token_id": c.after[source + "_eos_token_id"]})

    def save_adapter(destination):
        path = Path(destination)
        path.mkdir()
        (path / "adapter_config.json").write_text("{}\n")
        (path / "adapter_model.safetensors").write_bytes(b"synthetic adapter")

    model = SimpleNamespace(config=config("model"), generation_config=config("generation"),
        parameters=lambda: iter([SimpleNamespace(device=torch.device("cuda:0"))]),
        save_pretrained=save_adapter)

    def load(**kwargs):
        c.load_options = kwargs
        observe("load")
        if c.load_error:
            raise RuntimeError(c.load_error)
        return model, SimpleNamespace(pad_token_id=c.after["tokenizer_pad_token_id"],
                                      eos_token_id=c.after["tokenizer_eos_token_id"])

    def attach(model, **kwargs):
        observe("attach")
        return model

    class SyntheticTrainer:
        def __init__(self, **kwargs):
            self.state = SimpleNamespace(log_history=[], epoch=4)

        def train(self):
            observe("train")
            return SimpleNamespace(training_loss=1.0, global_step=100)

    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", lambda *a, **kw:
        SimpleNamespace(pad_token_id=c.before["tokenizer_pad_token_id"],
                        eos_token_id=c.before["tokenizer_eos_token_id"]))
    monkeypatch.setattr(unsloth.FastLanguageModel, "from_pretrained", load)
    monkeypatch.setattr(unsloth.FastLanguageModel, "get_peft_model", attach)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 0)
    monkeypatch.setattr(trainer, "granite_inventory", lambda *a:
        [{"lora_parameters": 140509184}] + [{"lora_parameters": 0}] * 447)
    monkeypatch.setattr(trainer, "granite_placement", lambda model: copy.deepcopy(c.placement))
    monkeypatch.setattr(trainer, "check_granite_adapter", lambda *a: 140509184)
    monkeypatch.setattr(trainer, "target_coverage", lambda *a:
        {name: {"present": 64, "adapted": 64} for name in granite.profile.TARGETS})
    monkeypatch.setattr(trainer, "component_coverage", lambda model: {})
    monkeypatch.setattr(trl, "SFTTrainer", SyntheticTrainer)
    monkeypatch.setattr(trl, "SFTConfig", lambda **kwargs: kwargs)

    def launch():
        for source, name in (("model", "config.json"), ("generation", "generation_config.json")):
            (c.root / name).write_text(json.dumps({"vocab_size": 64,
                "pad_token_id": c.before[source + "_pad_token_id"],
                "eos_token_id": c.before[source + "_eos_token_id"]}))
        manifest = mf.build_tree(c.root, "model", granite.profile.MODEL)
        manifest.save(c.manifest)
        data, _, _ = make_tokenized(tmp_path, manifest.tree_hash, length=32768)
        return trainer.train(c.root, c.manifest, data, granite.REPO / granite.CONFIG, c.out,
                             granite_probe_placement=c.expected_placement)

    c.launch = launch
    return c


def test_training_saves_ids_at_both_load_boundaries(training_load):
    c = training_load
    record = c.launch()
    expected = {"before": PADDING_BEFORE, "after": PADDING_AFTER}
    assert [event for event, _ in c.events] == ["load", "attach", "train"]
    assert c.events[0][1]["padding_token_ids"] == {"before": PADDING_BEFORE}
    assert all(values["padding_token_ids"] == expected for _, values in c.events[1:])
    assert record.status == "completed"
    assert record.results["padding_token_ids"] == expected
    assert mf.load_run(c.out / "run.json") == record
    assert record.results["reference_verified_after"] is True
    assert c.load_options["max_seq_length"] == 32768
    assert c.load_options["device_map"] == granite.profile.training_device_map()
    assert record.config["train"]["device_map"] == c.load_options["device_map"]
    assert record.results["placement_before"] == c.expected_placement
    assert record.results["placement_after"] == c.expected_placement


@pytest.mark.parametrize("phase", ["before", "after"])
@pytest.mark.parametrize("field", list(PADDING_BEFORE))
@pytest.mark.parametrize("value", [999, None])
def test_training_rejects_each_unapproved_id(training_load, phase, field, value):
    c = training_load
    getattr(c, phase)[field] = value
    reason = f"GRANITE-VERIFY: padding or end-of-turn IDs {phase} loading differ"
    with pytest.raises(ValueError, match=reason):
        c.launch()
    record = mf.load_run(c.out / "run.json")
    assert record.status == "failed"
    assert record.results["error"] == "ValueError: " + reason
    assert record.results["padding_token_ids"][phase][field] == value
    assert [event for event, _ in c.events] == ([] if phase == "before" else ["load"])
    assert not (c.out / "adapter").exists()
    assert mf.verify_tree(mf.load_tree(c.manifest), c.root) == []


def test_training_load_failure_keeps_only_observed_ids(training_load):
    c = training_load
    c.load_error = "fixture model load failed"
    with pytest.raises(RuntimeError, match=c.load_error):
        c.launch()
    record = mf.load_run(c.out / "run.json")
    assert record.status == "failed"
    assert record.results["error"] == "RuntimeError: " + c.load_error
    assert record.results["padding_token_ids"] == {"before": PADDING_BEFORE}
    assert [event for event, _ in c.events] == ["load"]
    assert not (c.out / "adapter").exists()
