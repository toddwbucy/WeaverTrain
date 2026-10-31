"""Load a reference model in bf16 across the visible GPUs, after verifying every file.

Every file's sha256 is checked against the model's manifest before anything is
loaded; any mismatch, missing or extra file refuses the load (ManifestError).
Models load with the transformers-native implementation (``trust_remote_code`` is
off), which is the implementation scripts/verify_env.py checks for kernel binding.
A model split across GPUs gets ``pin_layer_devices`` (see its docstring): without it
both reference models return NaN logits.

Model location, by precedence:
  1. an explicit directory (``load(path=...)``)
  2. ``$WEAVER_TRAIN_MODEL_DIR``
  3. ``$WEAVER_TRAIN_MODEL_CACHE/<local_dir>``, else ``$WEAVER_TRAIN_MODEL_STORE/<local_dir>``,
     with ``<local_dir>`` taken from the manifest.
Manifest: explicit path, else ``$WEAVER_TRAIN_MANIFEST_DIR/<name>.json``
(default: the repository's manifests/models/).

CLI::

    python -m weaver_train.loader NAME [--max-memory-gib 44,44]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from weaver_train import manifest as mf

DEFAULT_MANIFEST_DIR = Path(__file__).resolve().parents[2] / "manifests" / "models"


class LoadError(Exception):
    pass


@dataclass
class LoadedModel:
    model: object
    tokenizer: object
    name: str
    tree_hash: str
    path: Path
    device_map: dict
    devices: list[str]
    pinned_layers: int
    verify_seconds: float
    load_seconds: float

    @property
    def record(self) -> dict:
        """The ``model`` entry of a run record."""
        return {"name": self.name, "tree_hash": self.tree_hash}


def pin_layer_devices(model) -> int:
    """Make each decoder layer run with its own GPU as the current CUDA device.

    Works around a race found 2026-10-31 (docs/environment.md, Known gaps): mamba-ssm
    launches its Triton kernels on the *current* device's stream with no device
    guard. After a multi-GPU split, the current device stays cuda:0, so Mamba kernels
    for layers on cuda:1 run on cuda:0's stream, unordered with cuda:1's own work,
    and read unfinished inputs (NaN logits). A forward pre-hook per decoder layer sets
    the current device; it adds no synchronization. Backward needs nothing: autograd
    runs each device's work with that device current.

    Hooks every child of each ``nn.ModuleList`` named ``layers``. Idempotent.
    Returns the number of layers hooked. Kept identical in loader.py and trainer.py
    (modules share only weaver_train.manifest); tests/test_devices.py checks both.
    """
    import torch

    hooked = 0
    for name, mod in model.named_modules():
        if not (isinstance(mod, torch.nn.ModuleList) and name.rsplit(".", 1)[-1] == "layers"):
            continue
        for layer in mod:
            devices = {p.device for p in layer.parameters()}
            if len(devices) != 1:
                raise RuntimeError(f"decoder layer spans devices {devices}; cannot pin it")
            (device,) = devices
            if device.type != "cuda" or getattr(layer, "_weaver_pinned_device", None) == device:
                continue

            def pre_hook(module, args, _device=device):
                torch.cuda.set_device(_device)

            layer.register_forward_pre_hook(pre_hook)
            layer._weaver_pinned_device = device
            hooked += 1
    return hooked


def manifest_path_for(name: str) -> Path:
    return Path(os.environ.get("WEAVER_TRAIN_MANIFEST_DIR") or DEFAULT_MANIFEST_DIR) / f"{name}.json"


def resolve_dir(m: mf.TreeManifest, path: str | os.PathLike | None = None) -> Path:
    if path:
        return Path(path)
    if os.environ.get("WEAVER_TRAIN_MODEL_DIR"):
        return Path(os.environ["WEAVER_TRAIN_MODEL_DIR"])
    if not m.local_dir_name:
        raise LoadError(f"{m.name}: manifest has no local_dir_name; pass a path")
    for var in ("WEAVER_TRAIN_MODEL_CACHE", "WEAVER_TRAIN_MODEL_STORE"):
        root = os.environ.get(var)
        if root and (Path(root) / m.local_dir_name).is_dir():
            return Path(root) / m.local_dir_name
    raise LoadError(f"{m.name}: no model directory; set WEAVER_TRAIN_MODEL_CACHE or "
                    f"WEAVER_TRAIN_MODEL_STORE (see .env.example)")


def max_memory_map(gib: list[float] | None) -> dict | None:
    """Per-GPU caps, e.g. [44, 44] -> {0: '44GiB', 1: '44GiB'}. None lets accelerate decide."""
    if not gib:
        return None
    return {i: f"{g}GiB" for i, g in enumerate(gib)}


def load(name: str | None = None, *, path: str | os.PathLike | None = None,
         manifest_path: str | os.PathLike | None = None, max_memory_gib: list[float] | None = None,
         device_map: str | dict = "auto", attn_implementation: str | None = None,
         verify_workers: int = 8) -> LoadedModel:
    """Verify then load. ``name`` selects the manifest unless ``manifest_path`` is given."""
    if manifest_path is None:
        if name is None:
            raise LoadError("pass a model name or a manifest_path")
        manifest_path = manifest_path_for(name)
    m = mf.load_tree(manifest_path)
    if m.kind != "model":
        raise LoadError(f"{manifest_path} is a {m.kind} manifest, not a model")
    model_dir = resolve_dir(m, path)

    t0 = time.monotonic()
    tree_hash = mf.require_tree(m, model_dir, workers=verify_workers)  # refuses on mismatch
    verify_s = time.monotonic() - t0

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if not torch.cuda.is_available():
        raise LoadError("CUDA is not available")
    kwargs = dict(dtype=torch.bfloat16, device_map=device_map, trust_remote_code=False)
    mm = max_memory_map(max_memory_gib)
    if mm:
        kwargs["max_memory"] = mm
    if attn_implementation:
        kwargs["attn_implementation"] = attn_implementation

    t1 = time.monotonic()
    model = AutoModelForCausalLM.from_pretrained(str(model_dir), **kwargs)
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), trust_remote_code=False)
    load_s = time.monotonic() - t1
    model.eval()
    devices = sorted({str(p.device) for p in model.parameters()})
    pinned = pin_layer_devices(model) if len(devices) > 1 else 0

    wrong = {p.dtype for p in model.parameters() if p.is_floating_point() and p.dtype != torch.bfloat16}
    if wrong:
        raise LoadError(f"{m.name}: parameters not in bf16 after load: {wrong}")

    return LoadedModel(model=model, tokenizer=tokenizer, name=m.name, tree_hash=tree_hash,
                       path=model_dir, device_map=dict(getattr(model, "hf_device_map", {}) or {}),
                       devices=devices, pinned_layers=pinned,
                       verify_seconds=verify_s, load_seconds=load_s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m weaver_train.loader", description=__doc__.splitlines()[0])
    ap.add_argument("name", help="model name (manifest file stem)")
    ap.add_argument("--path", help="model directory (overrides env)")
    ap.add_argument("--manifest", help="manifest path (overrides name lookup)")
    ap.add_argument("--max-memory-gib", help="per-GPU caps, comma separated, e.g. 44,44")
    a = ap.parse_args(argv)
    caps = [float(x) for x in a.max_memory_gib.split(",")] if a.max_memory_gib else None
    try:
        lm = load(a.name, path=a.path, manifest_path=a.manifest, max_memory_gib=caps)
    except (LoadError, mf.ManifestError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    import torch

    print(f"{lm.name}: tree {lm.tree_hash[:16]} verified in {lm.verify_seconds:.0f}s, "
          f"loaded in {lm.load_seconds:.0f}s on {lm.devices}")
    for i in range(torch.cuda.device_count()):
        print(f"  cuda:{i} allocated {torch.cuda.memory_allocated(i) / 2**30:.1f} GiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
