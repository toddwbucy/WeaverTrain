import json
import textwrap

import pytest

from tests.conftest import NAME
from weaver_train import manifest as mf
from weaver_train import trainer

CONFIG = textwrap.dedent("""
    [lora]
    r = 4
    alpha = 8
    dropout = 0.0
    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj", "in_proj"]

    [train]
    max_seq_length = 64
    learning_rate = 1e-3
    max_steps = 2
    per_device_batch_size = 1
    gradient_accumulation_steps = 1
    warmup_steps = 0
    lr_scheduler = "constant"
    seed = 0
    gradient_checkpointing = false
    device_map = "auto"

    [data]
    format = "text"
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
    (lambda s: __import__("re").sub(r"(?m)^target_modules = .*\n", "", s, count=1),
     "target_modules is required"),
    (lambda s: s.replace("r = 4", "r = 0"), "positive"),
    (lambda s: s.replace("max_steps = 2", 'max_steps = "2"'), "wrong type"),
    (lambda s: s.replace("seed = 0", "seed = 0\nload_in_4bit = true"), "unknown keys"),
    (lambda s: s.replace("[data]", "[nodata]"), r"missing \[data\]"),
])
def test_config_rejects_incomplete_or_wrong(edit, msg):
    with pytest.raises(trainer.TrainConfigError, match=msg):
        trainer.parse_config(__import__("tomllib").loads(edit(CONFIG)))


@pytest.mark.parametrize("field, value, reason", [
    ("learning_rate", "true", "wrong type"),
    ("learning_rate", "false", "wrong type"),
    ("learning_rate", "nan", "must be finite"),
    ("learning_rate", "+inf", "must be finite"),
    ("learning_rate", "-inf", "must be finite"),
    ("dropout", "true", "wrong type"),
    ("dropout", "false", "wrong type"),
])
def test_train_stop_rejects_invalid_numeric_config(field, value, reason):
    """TRAIN-STOP: invalid numeric settings fail before a training procedure can start."""
    import tomllib
    original = "1e-3" if field == "learning_rate" else "0.0"
    text = CONFIG.replace(f"{field} = {original}", f"{field} = {value}")
    with pytest.raises(trainer.TrainConfigError, match=reason):
        trainer.parse_config(tomllib.loads(text))


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
    assert rec.model == {"name": NAME, "tree_hash": before}
    assert rec.results["global_step"] == 2 and rec.results["reference_verified_after"] is True
    names = {p.name for p in (out / "adapter").iterdir()}
    assert {"adapter_config.json", "adapter_model.safetensors"} <= names
    assert not names & {"model.safetensors", "config.json"}
    assert mf.load_tree(out / "adapter.manifest.json").tree_hash == rec.adapter["tree_hash"]
    assert mf.load_run(out / "run.json") == rec
    assert mf.require_tree(mf.load_tree(manifest_path), model_dir) == before

    # The decided target set (docs/decisions.md): attention plus Mamba in_proj, all adapted.
    assert rec.results["lora_coverage"] == {
        **{t: {"present": 1, "adapted": 1} for t in ("q_proj", "k_proj", "v_proj", "o_proj")},
        "in_proj": {"present": 2, "adapted": 2}}
    comp = rec.results["component_coverage"]
    assert {k: (c["layers"], c["adapted_layers"]) for k, c in comp.items()} == {
        "mamba": (2, 2), "attention": (1, 1), "moe": (1, 0)}


def _peft_on_cpu(model_dir, targets):
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM

    model = AutoModelForCausalLM.from_pretrained(model_dir, dtype=torch.bfloat16)
    return get_peft_model(model, LoraConfig(r=4, target_modules=targets))


@pytest.mark.parametrize("targets, adapted", [
    (["q_proj"], {"attention": 1, "mamba": 0, "moe": 0}),
    (["in_proj"], {"attention": 0, "mamba": 2, "moe": 0}),
    (["q_proj", "in_proj"], {"attention": 1, "mamba": 2, "moe": 0}),
])
def test_component_coverage_counts_layers_per_block_type(tiny_model, targets, adapted):
    """The stand-in has two Mamba layers, one attention layer and one MoE layer.

    The MoE's shared-expert MLP counts under the MoE, so no separate MLP kind appears.
    """
    model_dir, _ = tiny_model
    comp = trainer.component_coverage(_peft_on_cpu(model_dir, targets))
    assert {k: c["layers"] for k, c in comp.items()} == {"attention": 1, "mamba": 2, "moe": 1}
    assert {k: c["adapted_layers"] for k, c in comp.items()} == adapted
    assert all(c["params"] > 0 for c in comp.values())
    assert sum(c["adapted_params"] for c in comp.values()) > 0


@pytest.mark.gpu
def test_refuses_mamba_out_proj(tiny_model, dataset_path, config_path, tmp_path):
    """Known limit (2026-10-31, peft 0.21.2): PEFT refuses LoRA on NemotronH's Mamba
    `out_proj` (and `conv1d`) before attaching anything. The run fails and records it.

    If this starts failing because training succeeds, PEFT has lifted the limit: check
    the coverage numbers and retire this test.
    """
    model_dir, manifest_path = tiny_model
    config_path.write_text(config_path.read_text().replace('"in_proj"]', '"in_proj", "out_proj"]'))
    out = tmp_path / "run"
    with pytest.raises(ValueError, match="incompatible with Mamba"):
        trainer.train(model_dir, manifest_path, dataset_path, config_path, out)
    rec = mf.load_run(out / "run.json")
    assert rec.status == "failed" and "out_proj" in rec.results["error"]
    assert not (out / "adapter").exists()


@pytest.mark.parametrize("change, error", [
    (lambda s: s.replace('format = "text"\n', ''), 'format is required'),
    (lambda s: s.replace('format = "text"', 'format = "other"'), 'data.format'),
    (lambda s: s.replace('max_steps = 2', ''), 'exactly one'),
    (lambda s: s.replace('max_steps = 2', 'max_steps = 2\nnum_train_epochs = 2'), 'exactly one'),
    (lambda s: s.replace('per_device_batch_size = 1', 'per_device_batch_size = 2'), 'TRAIN-NO-PACKING'),
])
def test_train_tokenized_and_epochs_config_errors(change, error):
    """TRAIN-TOKENIZED, TRAIN-EPOCHS, TRAIN-NO-PACKING: refuse ambiguous settings."""
    import tomllib
    with pytest.raises(trainer.TrainConfigError, match=error):
        trainer.parse_config(tomllib.loads(change(CONFIG)))


def make_tokenized(tmp_path, model_hash, *, render_only=False, length=64):
    """TRAIN-TOKENIZED: tiny synthetic IDs for kernel tests, never adapter training data."""
    dest = tmp_path / "built"
    dest.mkdir()
    values = [4, 5, 6, 7, 8, 9, 10, 11]
    mask = [0, 0, 1, 1, 0, 0, 1, 1]
    rows = [{"input_ids": values, "assistant_masks": mask, "row": i} for i in range(2)]
    (dest / "train.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (dest / "build.json").write_text(json.dumps({"tree_hash": model_hash, "max_seq_length": length,
                                               "render_only": render_only}))
    mf.build_tree(dest, "dataset", "fixture").save(tmp_path / "built.manifest.json")
    return dest / "train.jsonl", values, mask


@pytest.mark.parametrize("case, reason", [
    ("missing_record", "build.json is required"),
    ("bad_manifest", "does not match manifest"),
    ("wrong_model", "tree_hash differs"),
    ("wrong_length", "max_seq_length differs"),
    ("render_only", "BUILD-RENDER-ONLY"),
])
def test_train_tokenized_rejects_invalid_build(tmp_path, case, reason):
    """TRAIN-TOKENIZED and BUILD-RENDER-ONLY: reject before model loading."""
    import tomllib
    cfg = trainer.parse_config(tomllib.loads(CONFIG.replace('format = "text"', 'format = "tokenized"')))
    path, _, _ = make_tokenized(tmp_path, "other" if case == "wrong_model" else "model",
                                render_only=case == "render_only", length=32 if case == "wrong_length" else 64)
    if case == "missing_record":
        path.with_name("build.json").unlink()
    elif case == "bad_manifest":
        path.write_text(path.read_text() + "\n")
    with pytest.raises((trainer.TrainConfigError, mf.ManifestError), match=reason):
        trainer.validate_tokenized(path, cfg, "model", 64)


@pytest.mark.parametrize("name", ["train.jsonl", "build.json"])
def test_train_tokenized_captured_bytes_must_match_manifest(tmp_path, monkeypatch, name):
    """TRAIN-TOKENIZED: a valid on-disk tree cannot attest to a different captured payload."""
    import tomllib
    cfg = trainer.parse_config(tomllib.loads(CONFIG.replace('format = "text"', 'format = "tokenized"')))
    path, _, _ = make_tokenized(tmp_path, "model")
    target = path.parent / name
    read_bytes = type(path).read_bytes
    captured = target.read_bytes() + b"\n"
    monkeypatch.setattr(type(path), "read_bytes", lambda item: captured if item == target else read_bytes(item))
    with pytest.raises(trainer.TrainConfigError, match=f"captured {name} does not match manifest"):
        trainer.validate_tokenized(path, cfg, "model", 64)


@pytest.mark.parametrize("filename, contents, reason", [
    ("build.json", "{", "build.json is not valid JSON"),
    ("build.json", "[]", "build.json must contain an object"),
    ("train.jsonl", "{", "row 1 is not valid JSON"),
    ("train.jsonl", "null", "row 1 must contain an object"),
    ("train.jsonl", '{"input_ids": [4, 64], "assistant_masks": [0, 1]}', "model vocabulary"),
])
def test_train_tokenized_rejects_malformed_records_before_load(
        tiny_model, config_path, tmp_path, monkeypatch, filename, contents, reason):
    """TRAIN-TOKENIZED: malformed records and out-of-vocabulary IDs cannot reach model loading."""
    model_dir, manifest_path = tiny_model
    path, _, _ = make_tokenized(tmp_path, mf.load_tree(manifest_path).tree_hash)
    (path.parent / filename).write_text(contents + "\n")
    mf.build_tree(path.parent, "dataset", "fixture").save(tmp_path / "built.manifest.json")
    config_path.write_text(CONFIG.replace('format = "text"', 'format = "tokenized"'))
    monkeypatch.setattr(trainer, "_run_unsloth", lambda *args: pytest.fail("invalid data reached model loading"))
    out = tmp_path / "invalid-run"
    with pytest.raises(trainer.TrainConfigError, match=reason):
        trainer.train(model_dir, manifest_path, path, config_path, out)
    record = mf.load_run(out / "run.json")
    assert record.status == "failed" and reason in record.results["error"]


@pytest.mark.gpu
def test_train_mask_reaches_loss_and_epoch_adapters(tiny_model, config_path, tmp_path, monkeypatch):
    """Section 9: inspect labels at the real GPU loss and verify two epoch adapters."""
    import torch
    model_dir, manifest_path = tiny_model
    path, ids, mask = make_tokenized(tmp_path, mf.load_tree(manifest_path).tree_hash)
    build_digest = mf.sha256_file(path.with_name("build.json"))
    data_digest = mf.sha256_file(path)
    config_path.write_text(CONFIG.replace('format = "text"', 'format = "tokenized"')
                          .replace('max_steps = 2', 'num_train_epochs = 2'))
    checked_class = trainer.checked_trainer_class
    observed = []

    def inspected(base):
        class Inspected(checked_class(base)):
            def compute_loss(self, model, inputs, *args, **kwargs):
                # TRAIN-MASK-REACHES-LOSS: observe the base model after trainer and PEFT processing.
                def at_forward(module, positional, keywords):
                    observed.append((keywords["input_ids"].detach().cpu().tolist(),
                                     keywords["labels"].detach().cpu().tolist()))
                handle = model.get_base_model().register_forward_pre_hook(at_forward, with_kwargs=True)
                try:
                    return super().compute_loss(model, inputs, *args, **kwargs)
                finally:
                    handle.remove()
        return Inspected

    monkeypatch.setattr(trainer, "checked_trainer_class", inspected)
    run_unsloth = trainer._run_unsloth

    def replace_source_after_validation(*args):
        # TRAIN-TOKENIZED, TRAIN-RECORD: loading uses captured bytes after the source is replaced.
        path.write_text("replaced dataset\n")
        path.with_name("build.json").write_text("{}\n")
        return run_unsloth(*args)

    monkeypatch.setattr(trainer, "_run_unsloth", replace_source_after_validation)
    out = tmp_path / "tokenized-run"
    record = trainer.train(model_dir, manifest_path, path, config_path, out)
    expected = [[value if keep else -100 for value, keep in zip(ids, mask)]]
    assert len(observed) == 4
    assert all(seen_ids == [ids] and labels == expected for seen_ids, labels in observed)
    for name in ("adapter-epoch-1", "adapter-epoch-2", "adapter"):
        trainer.check_adapter_only(out / name)
    assert record.dataset["build_sha256"] == build_digest
    assert record.dataset["sha256"] == data_digest
    assert record.dataset["sha256"] != mf.sha256_file(path)
    assert record.dataset["rows"] == 2 and record.dataset["assistant_tokens"] == 8
    assert record.results["epochs"] == 2 and record.results["global_step"] == 4
    assert len(record.results["step_seconds"]) == 4
    assert all(step["seconds"] > 0 for step in record.results["step_seconds"])
    assert record.results["wall_seconds"] > sum(step["seconds"] for step in record.results["step_seconds"])
    assert record.results["loss_history"] and record.results["last_loss"] >= 0
    assert len(record.results["peak_vram_gib"]) == torch.cuda.device_count()


@pytest.mark.parametrize("failure, reason", [
    ("oom", "test memory failure"),
    ("coverage", "test coverage failure"),
    ("cpu", "TRAIN-STOP: model parameters left GPUs"),
])
def test_train_stop_records_failures_without_retry(
        tiny_model, config_path, dataset_path, tmp_path, monkeypatch, failure, reason):
    """TRAIN-STOP: preserve measurements on failure and never retry a failed run."""
    import torch
    calls = []

    def fail(*args):
        calls.append(1)
        args[-1]["step_seconds"] = [{"step": 1, "seconds": 0.5}]
        if failure == "oom":
            raise torch.cuda.OutOfMemoryError("test memory failure")
        if failure == "coverage":
            raise trainer.TargetCoverageError("test coverage failure")
        raise RuntimeError("TRAIN-STOP: model parameters left GPUs")

    monkeypatch.setattr(trainer, "_run_unsloth", fail)
    out = tmp_path / "failed-run"
    expected = {"oom": torch.cuda.OutOfMemoryError, "coverage": trainer.TargetCoverageError,
                "cpu": RuntimeError}[failure]
    with pytest.raises(expected, match=reason):
        trainer.train(*tiny_model, dataset_path, config_path, out)
    assert calls == [1]
    record = mf.load_run(out / "run.json")
    assert record.status == "failed" and reason in record.results["error"]
    assert record.results["step_seconds"] == [{"step": 1, "seconds": 0.5}]
    assert not (out / "adapter").exists()


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_train_stop_checks_loss_before_backward(value, monkeypatch):
    """TRAIN-STOP: a non-finite microbatch cannot reach backward or an optimizer step."""
    import torch
    monkeypatch.setattr(trainer, "require_gpu_layers", lambda model: None)

    class Base:
        def compute_loss(self, model, inputs):
            return torch.tensor(value)

    with pytest.raises(RuntimeError, match="non-finite loss"):
        trainer.checked_trainer_class(Base)().compute_loss(None, {})


def test_train_stop_refuses_cpu_parameters():
    """TRAIN-STOP: a CPU layer is refused before training."""
    import torch
    with pytest.raises(RuntimeError, match="left GPUs"):
        trainer.require_gpu_layers(torch.nn.Linear(2, 2))
