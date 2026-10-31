import json
import textwrap

import pytest

from weaver_train import manifest as mf
from weaver_train import trainer

CONFIG = textwrap.dedent("""
    [lora]
    r = 4
    alpha = 8
    dropout = 0.0
    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj"]

    [train]
    max_seq_length = 64
    learning_rate = 1e-3
    max_steps = 2
    per_device_batch_size = 2
    gradient_accumulation_steps = 1
    warmup_steps = 0
    lr_scheduler = "constant"
    seed = 0
    gradient_checkpointing = false
    device_map = "auto"

    [data]
    text_field = "text"
""")


@pytest.fixture
def config_path(tmp_path):
    p = tmp_path / "train.toml"
    p.write_text(CONFIG)
    return p


@pytest.fixture
def dataset_path(tmp_path):
    """Test fixture only: four sentences over the stand-in vocabulary, not training data."""
    p = tmp_path / "fixture.jsonl"
    rows = ["the model state train", "a layer token test", "mamba attention expert route",
            "the cache shard device memory"]
    p.write_text("".join(json.dumps({"text": r}) + "\n" for r in rows))
    return p


def test_config_parses(config_path):
    cfg = trainer.load_config(config_path)
    assert cfg.lora.r == 4 and cfg.train.max_steps == 2 and cfg.text_field == "text"


@pytest.mark.parametrize("edit, msg", [
    (lambda s: s.replace('target_modules = ["q_proj", "k_proj", "v_proj", "o_proj"]\n', ""),
     "target_modules is required"),
    (lambda s: s.replace("r = 4", "r = 0"), "positive"),
    (lambda s: s.replace("max_steps = 2", 'max_steps = "2"'), "wrong type"),
    (lambda s: s.replace("seed = 0", "seed = 0\nload_in_4bit = true"), "unknown keys"),
    (lambda s: s.replace("[data]", "[nodata]"), r"missing \[data\]"),
])
def test_config_rejects_incomplete_or_wrong(edit, msg):
    with pytest.raises(trainer.TrainConfigError, match=msg):
        trainer.parse_config(__import__("tomllib").loads(edit(CONFIG)))


def test_refuses_out_dir_inside_reference(tiny_model, dataset_path, config_path):
    model_dir, manifest_path = tiny_model
    with pytest.raises(trainer.TrainConfigError, match="overlaps"):
        trainer.train(model_dir, manifest_path, dataset_path, config_path, model_dir / "lora")
    assert mf.verify_tree(mf.load_tree(manifest_path), model_dir) == []


def test_refuses_nonempty_out_dir(tiny_model, dataset_path, config_path, tmp_path):
    model_dir, manifest_path = tiny_model
    out = tmp_path / "used"
    out.mkdir()
    (out / "old").write_text("x")
    with pytest.raises(trainer.TrainConfigError, match="not empty"):
        trainer.train(model_dir, manifest_path, dataset_path, config_path, out)


def test_refuses_modified_reference(tiny_model, dataset_path, config_path, tmp_path):
    model_dir, manifest_path = tiny_model
    (model_dir / "generation_config.json").write_text("{}")
    with pytest.raises(mf.ManifestError):
        trainer.train(model_dir, manifest_path, dataset_path, config_path, tmp_path / "out")


@pytest.mark.gpu
def test_tiny_lora_writes_adapter_only(tiny_model, dataset_path, config_path, tmp_path):
    model_dir, manifest_path = tiny_model
    before = mf.load_tree(manifest_path).tree_hash
    out = tmp_path / "run"
    rec = trainer.train(model_dir, manifest_path, dataset_path, config_path, out)

    assert rec.status == "completed" and rec.engine == "python-hf" and rec.kind == "train"
    assert rec.model == {"name": "tiny-granite-hybrid", "tree_hash": before}
    assert rec.results["global_step"] == 2 and rec.results["reference_verified_after"] is True
    names = {p.name for p in (out / "adapter").iterdir()}
    assert {"adapter_config.json", "adapter_model.safetensors"} <= names
    assert not names & {"model.safetensors", "config.json"}
    assert mf.load_tree(out / "adapter.manifest.json").tree_hash == rec.adapter["tree_hash"]
    assert mf.load_run(out / "run.json") == rec
    assert mf.require_tree(mf.load_tree(manifest_path), model_dir) == before

    assert rec.results["lora_coverage"] == {t: {"present": 1, "adapted": 1}
                                            for t in ("q_proj", "k_proj", "v_proj", "o_proj")}
    comp = rec.results["component_coverage"]
    assert comp["attention"]["layers"] == comp["attention"]["adapted_layers"] == 1
    assert comp["mamba"] == {**comp["mamba"], "layers": 1, "adapted_layers": 0}


def _peft_on_cpu(model_dir, targets):
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM

    model = AutoModelForCausalLM.from_pretrained(model_dir, dtype=torch.bfloat16)
    return get_peft_model(model, LoraConfig(r=4, target_modules=targets))


@pytest.mark.parametrize("targets, adapted", [
    (["q_proj"], {"attention": 1, "mamba": 0, "moe": 0, "mlp": 0}),
    (["in_proj"], {"attention": 0, "mamba": 1, "moe": 0, "mlp": 0}),
    (["q_proj", "in_proj"], {"attention": 1, "mamba": 1, "moe": 0, "mlp": 0}),
])
def test_component_coverage_counts_layers_per_block_type(tiny_model, targets, adapted):
    """The stand-in has one Mamba and one attention layer, each with an MoE and a shared MLP."""
    model_dir, _ = tiny_model
    comp = trainer.component_coverage(_peft_on_cpu(model_dir, targets))
    assert {k: c["layers"] for k, c in comp.items()} == {"attention": 1, "mamba": 1, "moe": 2, "mlp": 2}
    assert {k: c["adapted_layers"] for k, c in comp.items()} == adapted
    assert all(c["params"] > 0 for c in comp.values())
    assert sum(c["adapted_params"] for c in comp.values()) > 0


@pytest.mark.gpu
def test_refuses_when_mamba_targets_are_dropped(tiny_model, dataset_path, config_path, tmp_path):
    """Known gap (2026-10-31): Unsloth's target regex skips Granite's `mamba.*_proj`.

    If this starts failing because training succeeds, Unsloth now covers Granite's
    Mamba blocks: check the coverage numbers and retire this test.
    """
    model_dir, manifest_path = tiny_model
    config_path.write_text(config_path.read_text().replace(
        '"o_proj"]', '"o_proj", "in_proj", "out_proj"]'))
    out = tmp_path / "run"
    with pytest.raises(trainer.TargetCoverageError, match=r"in_proj 0/1, out_proj 0/1"):
        trainer.train(model_dir, manifest_path, dataset_path, config_path, out)
    rec = mf.load_run(out / "run.json")
    assert rec.status == "failed" and "TargetCoverageError" in rec.results["error"]
    assert not (out / "adapter").exists()
