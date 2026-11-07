"""Thin LoRA wrapper over Unsloth. Writes adapters only; never touches the reference.

    train(model_dir, manifest_path, dataset_path, config_path, out_dir) -> RunRecord

Order of work:
  1. Parse and validate the config (TOML; schema below). Every LoRA and optimizer
     choice is required -- there are no silent defaults for decisions not yet made.
  2. Refuse an ``out_dir`` inside the model directory, or one that already holds files.
  3. Verify the model tree against its manifest (full sha256); hash the dataset file.
  4. Load in bf16 through Unsloth (no 4/8-bit), attach LoRA, train with TRL's SFTTrainer.
  5. Save the adapter to ``out_dir/adapter`` and confirm it contains adapter files only.
  6. Re-verify the model tree (``verify_reference_after``), so the record can state the
     reference was unchanged.
  7. Write ``out_dir/run.json`` (status ``completed`` or ``failed``).

Dataset: JSON Lines with explicit data.format, as Spec 0.3 TRAIN-TOKENIZED requires.
Text uses data.text_field. Tokenized rows use verified input_ids and assistant_masks.

Config schema (TOML)::

    [lora]
    r = 16
    alpha = 32
    dropout = 0.0
    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj"]
    bias = "none"                       # optional

    [train]
    max_seq_length = 32768
    learning_rate = 1e-4
    max_steps = 100
    per_device_batch_size = 1
    gradient_accumulation_steps = 8
    warmup_steps = 10
    lr_scheduler = "linear"
    seed = 0
    gradient_checkpointing = "unsloth"  # optional: "unsloth" | true | false
    logging_steps = 1                   # optional
    device_map = "balanced"             # optional: how Unsloth places layers on GPUs

    [data]
    format = "text"
    text_field = "text"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from weaver_train import manifest as mf

ADAPTER_FILES = {"adapter_config.json", "adapter_model.safetensors"}
ADAPTER_OPTIONAL = {"README.md"}


class TrainConfigError(Exception):
    pass


class TargetCoverageError(Exception):
    """A requested LoRA target exists in the model but received no adapter.

    Unsloth turns ``target_modules`` into a regex that only accepts modules under
    path segments such as ``self_attn``, ``mixer`` or ``mlp``. Granite-4.0-H names its
    Mamba blocks ``mamba``, so ``in_proj`` / ``out_proj`` there are dropped silently.
    The trainer refuses rather than train a different adapter than the config states.
    """


@dataclass
class LoraConfig:
    r: int
    alpha: int
    dropout: float
    target_modules: list[str]
    bias: str = "none"


@dataclass
class TrainSettings:
    max_seq_length: int
    learning_rate: float
    per_device_batch_size: int
    gradient_accumulation_steps: int
    warmup_steps: int
    lr_scheduler: str
    seed: int
    max_steps: int | None = None
    num_train_epochs: int | None = None
    gradient_checkpointing: str | bool = "unsloth"
    logging_steps: int = 1
    device_map: str = "balanced"


@dataclass
class TrainConfig:
    lora: LoraConfig
    train: TrainSettings
    data_format: str
    text_field: str | None = None
    raw: dict = field(default_factory=dict)


_REQUIRED = {
    "lora": {"r": int, "alpha": int, "dropout": (int, float), "target_modules": list},
    "train": {"max_seq_length": int, "learning_rate": (int, float),
              "per_device_batch_size": int, "gradient_accumulation_steps": int,
              "warmup_steps": int, "lr_scheduler": str, "seed": int},
    "data": {"format": str},
}
_OPTIONAL = {
    "lora": {"bias": str},
    "train": {"gradient_checkpointing": (str, bool), "logging_steps": int, "device_map": str,
              "max_steps": int, "num_train_epochs": int},
    "data": {"text_field": str},
}


def parse_config(raw: dict) -> TrainConfig:
    """TRAIN-TOKENIZED, TRAIN-EPOCHS, TRAIN-NO-PACKING: require explicit choices."""
    for section, fields in _REQUIRED.items():
        sec = raw.get(section)
        if not isinstance(sec, dict):
            raise TrainConfigError(f"missing [{section}] section")
        allowed = fields.keys() | _OPTIONAL[section].keys()
        unknown = sec.keys() - allowed
        if unknown:
            raise TrainConfigError(f"[{section}] unknown keys: {sorted(unknown)}")
        for key, typ in fields.items():
            if key not in sec:
                raise TrainConfigError(f"[{section}] {key} is required")
            if (not isinstance(sec[key], typ)
                    or isinstance(sec[key], bool) and (typ is int or typ == (int, float))):
                raise TrainConfigError(f"[{section}] {key} has the wrong type")
        for key, typ in _OPTIONAL[section].items():
            if key in sec and (not isinstance(sec[key], typ) or typ is int and isinstance(sec[key], bool)):
                raise TrainConfigError(f"[{section}] {key} has the wrong type")
    lora, tr = raw["lora"], raw["train"]
    if not lora["target_modules"] or not all(isinstance(t, str) for t in lora["target_modules"]):
        raise TrainConfigError("[lora] target_modules must be a non-empty list of names")
    if lora["r"] <= 0 or lora["alpha"] <= 0 or not 0 <= lora["dropout"] < 1:
        raise TrainConfigError("[lora] r and alpha must be positive, dropout in [0, 1)")
    limits = [key for key in ("max_steps", "num_train_epochs") if key in tr]
    if len(limits) != 1:
        raise TrainConfigError("TRAIN-EPOCHS: supply exactly one of max_steps or num_train_epochs")
    # TRAIN-STOP: reject a non-finite optimizer rate before loading weights.
    if not math.isfinite(tr["learning_rate"]):
        raise TrainConfigError("[train] learning_rate must be finite")
    if tr[limits[0]] <= 0 or tr["max_seq_length"] <= 0 or tr["learning_rate"] <= 0:
        raise TrainConfigError("[train] steps or epochs, max_seq_length and learning_rate must be positive")
    if tr["per_device_batch_size"] != 1:
        raise TrainConfigError("TRAIN-NO-PACKING: per_device_batch_size must be 1")
    data = raw["data"]
    if data["format"] not in ("text", "tokenized"):
        raise TrainConfigError("TRAIN-TOKENIZED: data.format must be text or tokenized")
    if data["format"] == "text" and not data.get("text_field"):
        raise TrainConfigError("TRAIN-TOKENIZED: text_field is required for text data")
    return TrainConfig(lora=LoraConfig(**lora), train=TrainSettings(**tr),
                       data_format=data["format"], text_field=data.get("text_field"), raw=raw)


def load_config(path: str | os.PathLike) -> TrainConfig:
    return parse_config(tomllib.loads(Path(path).read_text()))


def check_out_dir(out_dir: Path, model_dir: Path) -> None:
    out_r, model_r = out_dir.resolve(), model_dir.resolve()
    if out_r == model_r or model_r in out_r.parents or out_r in model_r.parents:
        raise TrainConfigError(f"out_dir {out_dir} overlaps the reference model directory")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise TrainConfigError(f"out_dir {out_dir} is not empty; each run gets a fresh directory")


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(16 * 2**20):
            h.update(chunk)
    return h.hexdigest()


def pin_layer_devices(model) -> int:
    """Make each decoder layer run with its own GPU as the current CUDA device.

    Works around a race found 2026-10-31 (docs/environment.md, Known gaps): mamba-ssm
    2.3.2.post1 launches one Triton kernel, ``_chunk_scan_fwd_kernel``, without the
    device guard its other launches have, so it runs on the *current* device's stream.
    After a multi-GPU split the current device stays cuda:0, so for layers on cuda:1
    that kernel runs on cuda:0's stream, unordered with cuda:1's own work, and reads
    unfinished inputs (NaN logits). A forward pre-hook per decoder layer sets the
    current device; it adds no synchronization. Backward needs nothing: autograd runs
    each device's work with that device current.

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


def target_coverage(model, targets: list[str]) -> dict[str, dict[str, int]]:
    """Per requested target: how many modules exist in the base model, how many got LoRA."""
    import torch

    cov = {t: {"present": 0, "adapted": 0} for t in targets}
    for name, mod in model.named_modules():
        leaf = name.rsplit(".", 1)[-1]
        if leaf not in cov:
            continue
        lora_a = getattr(mod, "lora_A", None)
        if lora_a is not None and len(lora_a) > 0:
            cov[leaf]["present"] += 1
            cov[leaf]["adapted"] += 1
        elif isinstance(mod, torch.nn.Linear):
            cov[leaf]["present"] += 1
    return cov


GRANITE_TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj",
                   "gate_proj", "up_proj", "down_proj")


def granite_inventory(model, targets, rank):
    """GRANITE-TARGETS: inventory every intended matrix before LoRA attachment."""
    import torch

    if tuple(targets) != GRANITE_TARGETS:
        raise TargetCoverageError("GRANITE-TARGETS: the seven target names differ")
    modules = dict(model.named_modules())
    inventory = []
    for layer in range(model.config.num_hidden_layers):
        for target in GRANITE_TARGETS:
            component = "self_attn" if target in GRANITE_TARGETS[:4] else "mlp"
            name = f"model.layers.{layer}.{component}.{target}"
            module = modules.get(name)
            if not isinstance(module, torch.nn.Linear) or hasattr(module, "lora_A"):
                raise TargetCoverageError(f"GRANITE-TARGETS: missing base linear {name}")
            inventory.append({"name": name, "in_features": module.in_features,
                              "out_features": module.out_features,
                              "lora_parameters": rank * (module.in_features + module.out_features)})
    return inventory


def check_granite_adapter(model, inventory, rank):
    """GRANITE-TARGETS: check each adapter, its dimensions and all trainable names."""
    modules = dict(model.named_modules())
    expected = set()
    for entry in inventory:
        name = "base_model.model." + entry["name"]
        module = modules.get(name)
        for side, shape in (("A", (rank, entry["in_features"])),
                            ("B", (entry["out_features"], rank))):
            adapters = getattr(module, f"lora_{side}", {})
            if set(adapters) != {"default"}:
                raise TargetCoverageError(f"GRANITE-TARGETS: missing or extra adapter at {name}")
            parameter = adapters["default"].weight
            if tuple(parameter.shape) != shape or not parameter.requires_grad:
                raise TargetCoverageError(f"GRANITE-TARGETS: adapter shape or training flag differs at {name}")
            expected.add(f"{name}.lora_{side}.default.weight")
    trainable = {name: p for name, p in model.named_parameters() if p.requires_grad}
    if set(trainable) != expected:
        raise TargetCoverageError("GRANITE-TARGETS: trainable parameters differ from the inventoried adapters")
    count = sum(p.numel() for p in trainable.values())
    if count != sum(entry["lora_parameters"] for entry in inventory):
        raise TargetCoverageError("GRANITE-TARGETS: trainable parameter count differs")
    return count


def granite_placement(model):
    """GRANITE-VERIFY: retain the native attention path and bf16 GPU placement."""
    import torch

    require_gpu_layers(model)
    for name, parameter in model.named_parameters():
        if ".lora_" not in name and parameter.is_floating_point() and parameter.dtype != torch.bfloat16:
            raise RuntimeError("GRANITE-VERIFY: reference weights are not bf16")
    return {"attention_implementation": model.config._attn_implementation,
            "parameter_devices": {name: str(p.device) for name, p in model.named_parameters()},
            "hf_device_map": {name: str(device) for name, device in
                              getattr(model, "hf_device_map", {}).items()}}


def granite_padding_ids(tokenizer, model_config, generation_config):
    """GRANITE-VERIFY: capture tokenizer and configuration IDs without changing them."""
    return {"tokenizer_pad_token_id": tokenizer.pad_token_id,
            "tokenizer_eos_token_id": tokenizer.eos_token_id,
            "model_pad_token_id": model_config.get("pad_token_id"),
            "model_eos_token_id": model_config.get("eos_token_id"),
            "generation_pad_token_id": generation_config.get("pad_token_id"),
            "generation_eos_token_id": generation_config.get("eos_token_id")}


def check_granite_padding_ids(values, *, loaded):
    """GRANITE-VERIFY: the probe and R1 share the exact approved ID checks."""
    expected = {f"{source}_{token}_token_id": 100261 if loaded and token == "pad" else 100257
                for source in ("tokenizer", "model", "generation") for token in ("pad", "eos")}
    phase = "after" if loaded else "before"
    if values != expected:
        raise ValueError(f"GRANITE-VERIFY: padding or end-of-turn IDs {phase} loading differ")


def record_granite_padding(record, metrics, out_dir, phase, tokenizer, model_config, generation_config):
    """GRANITE-VERIFY: save actual IDs before checking or continuing the load."""
    values = granite_padding_ids(tokenizer, model_config, generation_config)
    metrics.setdefault("padding_token_ids", {})[phase] = values
    record.results = metrics
    record.save(out_dir / "run.json")
    check_granite_padding_ids(values, loaded=phase == "after")


# Block types, matched against the class name of the outermost submodule of a decoder
# layer that names one, after removing the model-family prefix it shares with the layer
# class (GraniteMoeHybridMambaLayer -> MambaLayer, NemotronHMoE -> MoE): the Granite
# family name itself contains "Moe". An MoE's shared-expert MLP counts under the MoE.
COMPONENT_KINDS = ("mamba", "attention", "moe", "mlp")


def component_coverage(model) -> dict[str, dict[str, int]]:
    """Per block type: decoder layers containing it, how many got any LoRA, and parameters.

    Requested-target counts (target_coverage) cannot show a block type that no target
    names, such as Granite's Mamba blocks under attention-only targets, or MoE experts
    held as fused parameters. Two models compared after training must be compared with
    this record in view. ``params`` counts base weights, ``adapted_params`` LoRA weights.
    """
    import torch

    layers = next((m for n, m in model.named_modules()
                   if n.endswith("layers") and isinstance(m, torch.nn.ModuleList)), None)
    if layers is None:
        raise RuntimeError("no decoder layer list found in the model")
    cov = {k: {"layers": 0, "adapted_layers": 0, "params": 0, "adapted_params": 0} for k in COMPONENT_KINDS}
    for layer in layers:
        family = type(layer).__name__
        claimed: list[str] = []
        for name, mod in layer.named_modules():
            if not name or any(name == c or name.startswith(c + ".") for c in claimed):
                continue
            cls = type(mod).__name__
            cls = cls[len(os.path.commonprefix([cls, family])):].lower()
            kind = next((k for k in COMPONENT_KINDS if k in cls), None)
            if kind is None:
                continue
            claimed.append(name)
            base = sum(p.numel() for n, p in mod.named_parameters() if "lora_" not in n)
            lora = sum(p.numel() for n, p in mod.named_parameters() if "lora_" in n)
            c = cov[kind]
            c["layers"] += 1
            c["params"] += base
            c["adapted_params"] += lora
            c["adapted_layers"] += lora > 0
    return {k: c for k, c in cov.items() if c["layers"]}


def check_coverage(cov: dict[str, dict[str, int]]) -> None:
    absent = sorted(t for t, c in cov.items() if c["present"] == 0)
    dropped = {t: c for t, c in cov.items() if c["adapted"] < c["present"]}
    if absent:
        raise TargetCoverageError(f"target_modules not found in the model: {absent}")
    if dropped:
        raise TargetCoverageError(
            "LoRA was not attached to every requested module (Unsloth target filter): "
            + ", ".join(f"{t} {c['adapted']}/{c['present']}" for t, c in sorted(dropped.items())))


def check_adapter_only(adapter_dir: Path) -> None:
    names = {p.name for p in adapter_dir.iterdir()}
    missing = ADAPTER_FILES - names
    extra = names - ADAPTER_FILES - ADAPTER_OPTIONAL
    if missing or extra:
        raise RuntimeError(f"adapter output is not adapter-only: missing {sorted(missing)}, "
                           f"unexpected {sorted(extra)}")


def tokenized_object(text: str | bytes, context: str) -> dict:
    """TRAIN-TOKENIZED: reject malformed records before reading their fields."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TrainConfigError(f"TRAIN-TOKENIZED: {context} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise TrainConfigError(f"TRAIN-TOKENIZED: {context} must contain an object")
    return value


def validate_tokenized(dataset_path: Path, cfg: TrainConfig, model_hash: str, vocab_size: int,
                       *, snapshot: bytes | None = None) -> dict:
    """TRAIN-TOKENIZED: verify the build tree, model identity, length and masks."""
    parent = dataset_path.parent
    build_path = parent / "build.json"
    if not build_path.is_file():
        raise TrainConfigError("TRAIN-TOKENIZED: build.json is required")
    build_bytes = build_path.read_bytes()
    build = tokenized_object(build_bytes, "build.json")
    if build.get("render_only"):
        raise TrainConfigError("BUILD-RENDER-ONLY: trainer rejects render-only data")
    manifest_path = parent.with_name(parent.name + ".manifest.json")
    if not manifest_path.is_file():
        raise TrainConfigError("TRAIN-TOKENIZED: dataset manifest is required")
    manifest = mf.load_tree(manifest_path)
    mf.require_tree(manifest, parent)
    if snapshot is None:
        snapshot = dataset_path.read_bytes()
    expected = {entry.path: entry.sha256 for entry in manifest.files}
    for path, data in ((dataset_path, snapshot), (build_path, build_bytes)):
        if hashlib.sha256(data).hexdigest() != expected.get(path.relative_to(parent).as_posix()):
            raise TrainConfigError(f"TRAIN-TOKENIZED: captured {path.name} does not match manifest")
    if build.get("tree_hash") != model_hash:
        raise TrainConfigError("TRAIN-TOKENIZED: model tree_hash differs from build.json")
    if build.get("max_seq_length") != cfg.train.max_seq_length:
        raise TrainConfigError("TRAIN-TOKENIZED: max_seq_length differs from build.json")
    rows = 0
    assistant_tokens = 0
    for number, line in enumerate(snapshot.splitlines(), 1):
        if not line.strip():
            continue
        row = tokenized_object(line, f"row {number}")
        ids, masks = row.get("input_ids"), row.get("assistant_masks")
        if not isinstance(ids, list) or not isinstance(masks, list) or len(ids) != len(masks):
            raise TrainConfigError("TRAIN-MASK-REACHES-LOSS: IDs and masks must have equal lengths")
        if not ids or len(ids) > cfg.train.max_seq_length:
            raise TrainConfigError("TRAIN-NO-PACKING: an intact row must fit max_seq_length")
        if any(type(value) is not int or not 0 <= value < vocab_size for value in ids):
            raise TrainConfigError("TRAIN-TOKENIZED: token IDs must be integers in the model vocabulary")
        if any(type(value) is not int or value not in (0, 1) for value in masks) or not any(masks[1:]):
            raise TrainConfigError("TRAIN-MASK-REACHES-LOSS: binary masks need a target after token zero")
        rows += 1
        assistant_tokens += sum(masks)
    if not rows:
        raise TrainConfigError("TRAIN-TOKENIZED: dataset has no rows")
    return {"build_sha256": hashlib.sha256(build_bytes).hexdigest(), "rows": rows,
            "assistant_tokens": assistant_tokens}


def tokenized_dataset(snapshot: bytes):
    """TRAIN-TOKENIZED, TRAIN-RECORD: load the same captured bytes used for validation and hashing."""
    from datasets import Dataset
    columns = {"input_ids": [], "assistant_masks": []}
    for number, line in enumerate(snapshot.splitlines(), 1):
        if line.strip():
            row = tokenized_object(line, f"row {number}")
            for key in columns:
                columns[key].append(row[key])
    return Dataset.from_dict(columns)


class AssistantCollator:
    """LOSS-ASSISTANT, LOSS-UNWEIGHTED, TRAIN-NO-PACKING: label one intact sequence."""

    def __call__(self, examples):
        import torch
        if len(examples) != 1:
            raise TrainConfigError("TRAIN-NO-PACKING: expected one row per batch")
        row = examples[0]
        ids = torch.tensor([row["input_ids"]], dtype=torch.long)
        mask = torch.tensor([row["assistant_masks"]], dtype=torch.bool)
        labels = ids.clone()
        labels[~mask] = -100
        return {"input_ids": ids, "attention_mask": torch.ones_like(ids), "labels": labels}


def require_gpu_layers(model):
    """TRAIN-STOP: no reference or adapter parameter may reside on CPU or meta."""
    devices = {parameter.device.type for parameter in model.parameters()}
    if devices != {"cuda"}:
        raise RuntimeError(f"TRAIN-STOP: model parameters left GPUs: {sorted(devices)}")


def checked_trainer_class(base):
    """TRAIN-STOP: inspect the real loss before backward, including each microbatch."""
    class CheckedTrainer(base):
        def compute_loss(self, model, inputs, *args, **kwargs):
            import torch
            require_gpu_layers(model)
            result = super().compute_loss(model, inputs, *args, **kwargs)
            loss = result[0] if isinstance(result, tuple) else result
            if not torch.isfinite(loss).all().item():
                raise RuntimeError("TRAIN-STOP: non-finite loss")
            return result
    return CheckedTrainer


def train(model_dir: str | os.PathLike, manifest_path: str | os.PathLike,
          dataset_path: str | os.PathLike, config_path: str | os.PathLike,
          out_dir: str | os.PathLike, *, verify_reference_after: bool = True,
          verify_workers: int = 8, granite_probe_placement: dict | None = None) -> mf.RunRecord:
    """TRAIN-RECORD, TRAIN-STOP: persist measurements and failures without retry."""
    started = time.monotonic()
    model_dir, out_dir, dataset_path = Path(model_dir), Path(out_dir), Path(dataset_path)
    cfg = load_config(config_path)
    check_out_dir(out_dir, model_dir)
    m = mf.load_tree(manifest_path)
    if m.kind != "model":
        raise TrainConfigError(f"{manifest_path} is not a model manifest")
    tree_hash = mf.require_tree(m, model_dir, workers=verify_workers)
    out_dir.mkdir(parents=True, exist_ok=True)
    record = mf.RunRecord(
        kind="train", model={"name": m.name, "tree_hash": tree_hash}, seed=cfg.train.seed,
        dataset={"file_name": dataset_path.name}, config=cfg.raw,
    )
    metrics = {}
    if m.name == "granite-4.2-30b":
        metrics["required_target_counts"] = {"modules": 448, "parameters": 140509184}
        metrics["required_granite_placement"] = granite_probe_placement
    record.save(out_dir / "run.json")
    try:
        if m.name == "granite-4.2-30b" and granite_probe_placement is None:
            raise TrainConfigError("GRANITE-MEMORY: probe placement is required")
        dataset_input = dataset_path
        if cfg.data_format == "tokenized":
            dataset_input = dataset_path.read_bytes()
            model_config = json.loads((model_dir / "config.json").read_text())
            record.dataset.update(validate_tokenized(dataset_path, cfg, tree_hash, model_config["vocab_size"],
                                                       snapshot=dataset_input))
            record.dataset["sha256"] = hashlib.sha256(dataset_input).hexdigest()
        else:
            record.dataset["sha256"] = sha256_path(dataset_path)
        options = {"granite_record": record} if m.name == "granite-4.2-30b" else {}
        _run_unsloth(model_dir, dataset_input, cfg, out_dir, metrics, **options)
        adapter_dir = out_dir / "adapter"
        check_adapter_only(adapter_dir)
        am = mf.build_tree(adapter_dir, "model", f"{m.name}-lora-{record.run_id[:8]}")
        am.save(out_dir / "adapter.manifest.json")
        record.adapter = {"name": am.name, "tree_hash": am.tree_hash}
        if verify_reference_after:
            mf.require_tree(m, model_dir, workers=verify_workers)
            metrics["reference_verified_after"] = True
        record.status = "completed"
    except BaseException as exc:
        record.status = "failed"
        metrics["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        metrics["wall_seconds"] = time.monotonic() - started
        record.results = metrics
        record.save(out_dir / "run.json")
    return record


def _run_unsloth(model_dir: Path, dataset_input: Path | bytes, cfg: TrainConfig, out_dir: Path,
                 metrics: dict, *, granite_record: mf.RunRecord | None = None) -> None:
    """TRAIN-MASK-REACHES-LOSS, TRAIN-EPOCH-ADAPTERS, TRAIN-RECORD: train and measure."""
    import unsloth  # noqa: F401
    from unsloth import FastLanguageModel
    import torch
    from datasets import load_dataset
    from transformers import AutoTokenizer, TrainerCallback
    from trl import SFTConfig, SFTTrainer

    t = cfg.train
    metrics.update(step_seconds=[], loss_history=[], epoch_adapters=[])

    def sync():
        for device in range(torch.cuda.device_count()):
            torch.cuda.synchronize(device)

    class Measurements(TrainerCallback):
        def on_step_begin(self, args, state, control, **kwargs):
            sync()
            self.step_start = time.monotonic()

        def on_step_end(self, args, state, control, **kwargs):
            sync()
            metrics["step_seconds"].append({"step": state.global_step,
                                            "seconds": time.monotonic() - self.step_start})
            metrics.update(global_step=state.global_step, epochs=state.epoch)

        def on_log(self, args, state, control, logs=None, **kwargs):
            if logs and "loss" in logs:
                if not math.isfinite(logs["loss"]):
                    raise RuntimeError("TRAIN-STOP: non-finite logged loss")
                metrics["loss_history"].append({"step": state.global_step, "epoch": state.epoch,
                                                "loss": logs["loss"]})
                metrics["last_loss"] = logs["loss"]

        def on_epoch_end(self, args, state, control, model=None, **kwargs):
            if t.num_train_epochs is None:
                return
            number = round(state.epoch)
            dest = out_dir / f"adapter-epoch-{number}"
            model.save_pretrained(str(dest))
            check_adapter_only(dest)
            manifest = mf.build_tree(dest, "model", dest.name)
            manifest.save(out_dir / f"{dest.name}.manifest.json")
            metrics["epoch_adapters"].append({"epoch": number, "tree_hash": manifest.tree_hash})

    for device in range(torch.cuda.device_count()):
        torch.cuda.reset_peak_memory_stats(device)
    try:
        if granite_record is not None:
            tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True,
                                                     trust_remote_code=False)
            record_granite_padding(granite_record, metrics, out_dir, "before", tokenizer,
                json.loads((model_dir / "config.json").read_bytes()),
                json.loads((model_dir / "generation_config.json").read_bytes()))
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=str(model_dir), max_seq_length=t.max_seq_length, dtype=torch.bfloat16,
            load_in_4bit=False, load_in_8bit=False, full_finetuning=False,
            device_map=t.device_map, offload_embedding=False,
            trust_remote_code=False, local_files_only=True,
        )
        if granite_record is not None:
            record_granite_padding(granite_record, metrics, out_dir, "after", tokenizer,
                                   model.config.to_dict(), model.generation_config.to_dict())
        require_gpu_layers(model)
        granite = model.config.model_type == "granite"
        if "required_target_counts" in metrics and not granite:
            raise TargetCoverageError("GRANITE-MODEL: training implementation is not dense Granite")
        if granite:
            metrics["target_inventory"] = granite_inventory(model, cfg.lora.target_modules, cfg.lora.r)
            if "required_target_counts" in metrics:
                expected = metrics["required_target_counts"]
                if len(metrics["target_inventory"]) != expected["modules"] or sum(
                        row["lora_parameters"] for row in metrics["target_inventory"]) != expected["parameters"]:
                    raise TargetCoverageError("GRANITE-TARGETS: pinned module or parameter count differs")
            metrics["placement_before"] = granite_placement(model)
            if "required_granite_placement" in metrics and metrics["placement_before"] != metrics["required_granite_placement"]:
                raise RuntimeError("GRANITE-MEMORY: training placement or attention path differs from the probe")
        model = FastLanguageModel.get_peft_model(
            model, r=cfg.lora.r, lora_alpha=cfg.lora.alpha, lora_dropout=cfg.lora.dropout,
            target_modules=cfg.lora.target_modules, bias=cfg.lora.bias,
            use_gradient_checkpointing=t.gradient_checkpointing, random_state=t.seed,
        )
        if len({p.device for p in model.parameters()}) > 1:
            pin_layer_devices(model)
        if granite:
            metrics["trainable_parameters"] = check_granite_adapter(
                model, metrics["target_inventory"], cfg.lora.r)
            metrics["placement_after"] = granite_placement(model)
        coverage = target_coverage(model, cfg.lora.target_modules)
        check_coverage(coverage)
        metrics.update(lora_coverage=coverage, component_coverage=component_coverage(model))
        ds = (tokenized_dataset(dataset_input) if cfg.data_format == "tokenized"
              else load_dataset("json", data_files=str(dataset_input), split="train"))
        if cfg.data_format == "text" and cfg.text_field not in ds.column_names:
            raise TrainConfigError(f"dataset has no field {cfg.text_field!r}")
        metrics["rows"] = len(ds)
        options = {"max_steps": t.max_steps} if t.max_steps is not None else {"num_train_epochs": t.num_train_epochs}
        tokenized = cfg.data_format == "tokenized"
        trainer = checked_trainer_class(SFTTrainer)(
            model=model, processing_class=tokenizer, train_dataset=ds,
            data_collator=AssistantCollator() if tokenized else None,
            callbacks=[Measurements()],
            args=SFTConfig(
                output_dir=str(out_dir / "trainer"), dataset_text_field=cfg.text_field or "text",
                dataset_kwargs={"skip_prepare_dataset": True} if tokenized else None,
                remove_unused_columns=False, packing=False, padding_free=False,
                max_length=t.max_seq_length, per_device_train_batch_size=1,
                gradient_accumulation_steps=t.gradient_accumulation_steps,
                learning_rate=t.learning_rate, warmup_steps=t.warmup_steps,
                lr_scheduler_type=t.lr_scheduler, seed=t.seed, bf16=True, fp16=False,
                logging_steps=t.logging_steps, logging_nan_inf_filter=False,
                save_strategy="no", report_to=[], **options,
            ),
        )
        result = trainer.train()
        if not math.isfinite(result.training_loss):
            raise RuntimeError("TRAIN-STOP: non-finite final loss")
        model.save_pretrained(str(out_dir / "adapter"))
        (out_dir / "trainer").mkdir(exist_ok=True)
        (out_dir / "trainer" / "log_history.json").write_text(json.dumps(trainer.state.log_history, indent=1) + "\n")
        metrics.update(train_loss=result.training_loss, global_step=result.global_step,
                       epochs=trainer.state.epoch)
    finally:
        metrics["peak_vram_gib"] = {f"cuda:{i}": torch.cuda.max_memory_allocated(i) / 2**30
                                     for i in range(torch.cuda.device_count())}
        if "target_inventory" in metrics:
            metrics["peak_memory_bytes"] = {str(i): {
                "allocated": torch.cuda.max_memory_allocated(i),
                "reserved": torch.cuda.max_memory_reserved(i)}
                for i in range(torch.cuda.device_count())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m weaver_train.trainer", description=__doc__.splitlines()[0])
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        rec = train(a.model_dir, a.manifest, a.dataset, a.config, a.out)
    except (TrainConfigError, TargetCoverageError, mf.ManifestError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"run_id": rec.run_id, "status": rec.status, "adapter": rec.adapter,
                      "results": rec.results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
