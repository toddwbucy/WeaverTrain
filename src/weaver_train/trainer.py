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

Dataset: JSON Lines, one object per line, with the text in ``data.text_field``.
This is provisional: no training data has been chosen yet.

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
    text_field = "text"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
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
    max_steps: int
    per_device_batch_size: int
    gradient_accumulation_steps: int
    warmup_steps: int
    lr_scheduler: str
    seed: int
    gradient_checkpointing: str | bool = "unsloth"
    logging_steps: int = 1
    device_map: str = "balanced"


@dataclass
class TrainConfig:
    lora: LoraConfig
    train: TrainSettings
    text_field: str
    raw: dict = field(default_factory=dict)


_REQUIRED = {
    "lora": {"r": int, "alpha": int, "dropout": (int, float), "target_modules": list},
    "train": {"max_seq_length": int, "learning_rate": (int, float), "max_steps": int,
              "per_device_batch_size": int, "gradient_accumulation_steps": int,
              "warmup_steps": int, "lr_scheduler": str, "seed": int},
    "data": {"text_field": str},
}
_OPTIONAL = {
    "lora": {"bias": str},
    "train": {"gradient_checkpointing": (str, bool), "logging_steps": int, "device_map": str},
    "data": {},
}


def parse_config(raw: dict) -> TrainConfig:
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
            if not isinstance(sec[key], typ) or isinstance(sec[key], bool) and typ is int:
                raise TrainConfigError(f"[{section}] {key} has the wrong type")
        for key, typ in _OPTIONAL[section].items():
            if key in sec and not isinstance(sec[key], typ):
                raise TrainConfigError(f"[{section}] {key} has the wrong type")
    lora, tr = raw["lora"], raw["train"]
    if not lora["target_modules"] or not all(isinstance(t, str) for t in lora["target_modules"]):
        raise TrainConfigError("[lora] target_modules must be a non-empty list of names")
    if lora["r"] <= 0 or lora["alpha"] <= 0 or not 0 <= lora["dropout"] < 1:
        raise TrainConfigError("[lora] r and alpha must be positive, dropout in [0, 1)")
    if tr["max_steps"] <= 0 or tr["max_seq_length"] <= 0 or tr["learning_rate"] <= 0:
        raise TrainConfigError("[train] max_steps, max_seq_length and learning_rate must be positive")
    return TrainConfig(lora=LoraConfig(**lora), train=TrainSettings(**tr),
                       text_field=raw["data"]["text_field"], raw=raw)


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


def train(model_dir: str | os.PathLike, manifest_path: str | os.PathLike,
          dataset_path: str | os.PathLike, config_path: str | os.PathLike,
          out_dir: str | os.PathLike, *, verify_reference_after: bool = True,
          verify_workers: int = 8) -> mf.RunRecord:
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
        dataset={"file_name": dataset_path.name, "sha256": sha256_path(dataset_path)},
        config=cfg.raw,
    )
    record.save(out_dir / "run.json")
    try:
        metrics = _run_unsloth(model_dir, dataset_path, cfg, out_dir)
        adapter_dir = out_dir / "adapter"
        check_adapter_only(adapter_dir)
        am = mf.build_tree(adapter_dir, "model", f"{m.name}-lora-{record.run_id[:8]}")
        am.save(out_dir / "adapter.manifest.json")
        record.adapter = {"name": am.name, "tree_hash": am.tree_hash}
        if verify_reference_after:
            mf.require_tree(m, model_dir, workers=verify_workers)
            metrics["reference_verified_after"] = True
        record.results = metrics
        record.status = "completed"
    except BaseException as e:
        record.status = "failed"
        record.results = {"error": f"{type(e).__name__}: {e}"}
        record.save(out_dir / "run.json")
        raise
    record.save(out_dir / "run.json")
    return record


def _run_unsloth(model_dir: Path, dataset_path: Path, cfg: TrainConfig, out_dir: Path) -> dict:
    # Unsloth must be imported before transformers/trl/peft so its patches apply.
    import unsloth  # noqa: F401
    from unsloth import FastLanguageModel

    import torch
    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer

    t = cfg.train
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=str(model_dir), max_seq_length=t.max_seq_length, dtype=torch.bfloat16,
        load_in_4bit=False, load_in_8bit=False, full_finetuning=False,
        device_map=t.device_map, trust_remote_code=False, local_files_only=True,
    )
    model = FastLanguageModel.get_peft_model(
        model, r=cfg.lora.r, lora_alpha=cfg.lora.alpha, lora_dropout=cfg.lora.dropout,
        target_modules=cfg.lora.target_modules, bias=cfg.lora.bias,
        use_gradient_checkpointing=t.gradient_checkpointing, random_state=t.seed,
    )
    coverage = target_coverage(model, cfg.lora.target_modules)
    check_coverage(coverage)  # before any training step
    ds = load_dataset("json", data_files=str(dataset_path), split="train")
    if cfg.text_field not in ds.column_names:
        raise TrainConfigError(f"dataset has no field {cfg.text_field!r}; columns {ds.column_names}")

    for i in range(torch.cuda.device_count()):
        torch.cuda.reset_peak_memory_stats(i)
    trainer = SFTTrainer(
        model=model, processing_class=tokenizer, train_dataset=ds,
        args=SFTConfig(
            output_dir=str(out_dir / "trainer"), dataset_text_field=cfg.text_field,
            max_length=t.max_seq_length, per_device_train_batch_size=t.per_device_batch_size,
            gradient_accumulation_steps=t.gradient_accumulation_steps, max_steps=t.max_steps,
            learning_rate=t.learning_rate, warmup_steps=t.warmup_steps,
            lr_scheduler_type=t.lr_scheduler, seed=t.seed, bf16=True, fp16=False,
            logging_steps=t.logging_steps, save_strategy="no", report_to=[],
        ),
    )
    out = trainer.train()
    model.save_pretrained(str(out_dir / "adapter"))  # PEFT model: adapter weights only
    # The trainer directory holds logs only (save_strategy="no"); keep its log history.
    (out_dir / "trainer").mkdir(exist_ok=True)
    (out_dir / "trainer" / "log_history.json").write_text(
        json.dumps(trainer.state.log_history, indent=1) + "\n")
    return {
        "lora_coverage": coverage,
        "train_loss": out.training_loss,
        "global_step": out.global_step,
        "peak_vram_gib": {f"cuda:{i}": round(torch.cuda.max_memory_allocated(i) / 2**30, 2)
                          for i in range(torch.cuda.device_count())},
    }


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
