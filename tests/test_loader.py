import os

import pytest

from tests.conftest import NAME, SPLIT
from weaver_train import loader
from weaver_train import manifest as mf


def test_refuses_on_hash_mismatch_before_loading(tiny_model, monkeypatch):
    model_dir, manifest_path = tiny_model
    cfg = model_dir / "config.json"
    cfg.write_text(cfg.read_text().replace('"hidden_size": 64', '"hidden_size": 64 '))

    import transformers

    def boom(*a, **k):
        raise AssertionError("from_pretrained must not be reached")

    monkeypatch.setattr(transformers.AutoModelForCausalLM, "from_pretrained", boom)
    with pytest.raises(mf.ManifestError, match="config.json"):
        loader.load(manifest_path=manifest_path, path=model_dir)


def test_refuses_on_extra_file(tiny_model):
    model_dir, manifest_path = tiny_model
    (model_dir / "stray.bin").write_bytes(b"x")
    with pytest.raises(mf.ManifestError, match="unexpected: stray.bin"):
        loader.load(manifest_path=manifest_path, path=model_dir)


def test_resolves_directory_from_env(tiny_model, monkeypatch):
    model_dir, manifest_path = tiny_model
    m = mf.load_tree(manifest_path)
    monkeypatch.delenv("WEAVER_TRAIN_MODEL_DIR", raising=False)
    monkeypatch.setenv("WEAVER_TRAIN_MODEL_CACHE", str(model_dir.parent / "nonexistent"))
    monkeypatch.setenv("WEAVER_TRAIN_MODEL_STORE", str(model_dir.parent))
    assert loader.resolve_dir(m) == model_dir  # cache lacks it, falls back to store
    monkeypatch.setenv("WEAVER_TRAIN_MODEL_CACHE", str(model_dir.parent))
    assert loader.resolve_dir(m) == model_dir
    monkeypatch.setenv("WEAVER_TRAIN_MODEL_DIR", "/explicit")
    assert str(loader.resolve_dir(m)) == "/explicit"


def test_unresolvable_directory(tiny_model, monkeypatch):
    _, manifest_path = tiny_model
    for v in ("WEAVER_TRAIN_MODEL_DIR", "WEAVER_TRAIN_MODEL_CACHE", "WEAVER_TRAIN_MODEL_STORE"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(loader.LoadError, match="WEAVER_TRAIN_MODEL"):
        loader.resolve_dir(mf.load_tree(manifest_path))


def test_manifest_lookup_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv("WEAVER_TRAIN_MANIFEST_DIR", str(tmp_path))
    assert loader.manifest_path_for("x") == tmp_path / "x.json"


def test_rejects_snapshot_manifest(tmp_path):
    (tmp_path / "f").write_text("x")
    m = mf.build_tree(tmp_path, "snapshot", "s")
    p = tmp_path.parent / "s.json"
    m.save(p)
    with pytest.raises(loader.LoadError, match="not a model"):
        loader.load(manifest_path=p, path=tmp_path)


@pytest.mark.gpu
def test_load_forward_bf16(tiny_model):
    import torch

    model_dir, manifest_path = tiny_model
    lm = loader.load(manifest_path=manifest_path, path=model_dir)
    assert lm.tree_hash == mf.load_tree(manifest_path).tree_hash
    assert lm.record == {"name": NAME, "tree_hash": lm.tree_hash}
    assert lm.devices  # parameters landed on at least one GPU
    assert all(p.dtype == torch.bfloat16 for p in lm.model.parameters())
    ids = lm.tokenizer("the model state train", return_tensors="pt").input_ids.to("cuda:0")
    with torch.no_grad():
        logits = lm.model(ids).logits
    assert logits.shape[:2] == ids.shape and torch.isfinite(logits).all()


@pytest.mark.multigpu
def test_splits_across_two_gpus(tiny_model):
    """One model, layers on both cards, forward and generate across the split.

    The stand-in is too small for device_map="auto" to split it (accelerate keeps a
    model that fits on one card there), so the placement is explicit. Whether "auto"
    splits the 30B models is checked by the per-model smoke test, not here. Passing
    here does not show the split is race-free: see tests/test_devices.py.
    """
    import torch

    model_dir, manifest_path = tiny_model
    lm = loader.load(manifest_path=manifest_path, path=model_dir, device_map=SPLIT)
    assert lm.devices == ["cuda:0", "cuda:1"]
    ids = lm.tokenizer("the model", return_tensors="pt").input_ids.to("cuda:0")
    with torch.no_grad():
        out = lm.model.generate(ids, max_new_tokens=4, do_sample=False)
    assert out.shape[1] == ids.shape[1] + 4
