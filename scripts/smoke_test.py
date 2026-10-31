"""Smoke test one reference model: does it load, run, generate, and fit a 32K LoRA step?

    python scripts/smoke_test.py NAME [--seq-len 32768] [--targets q_proj,k_proj,...]

Stages, each stopping the script on failure (no fallbacks, no retries at lower settings):
  1. load     weaver_train.loader: full sha256 check, bf16, device_map="auto" over the
              visible GPUs. Records placement, timings, memory per GPU.
  2. forward  one forward pass on a short prompt; logits must be finite.
  3. generate greedy generation of --gen-tokens from a chat-templated prompt.
  4. lora     fresh load through Unsloth (bf16, device_map="balanced", embeddings kept
              on GPU), LoRA attached to --targets (coverage checked as in the trainer),
              then ONE optimizer step on a single --seq-len sequence of random token
              ids. Random ids, not data: this measures memory, not learning.
              Records peak allocated and reserved memory per GPU.

Writes $WEAVER_TRAIN_STATE_DIR/smoke/<name>-<UTC timestamp>.json and prints it.
Not a run record: nothing here is a training or benchmark result.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

PROMPT = "Write a Rust function that returns the n-th Fibonacci number iteratively."


def gpu_mem(torch) -> dict:
    return {f"cuda:{i}": {
        "allocated_gib": round(torch.cuda.memory_allocated(i) / 2**30, 2),
        "peak_allocated_gib": round(torch.cuda.max_memory_allocated(i) / 2**30, 2),
        "peak_reserved_gib": round(torch.cuda.max_memory_reserved(i) / 2**30, 2),
        "total_gib": round(torch.cuda.get_device_properties(i).total_memory / 2**30, 2),
    } for i in range(torch.cuda.device_count())}


def reset_peaks(torch) -> None:
    for i in range(torch.cuda.device_count()):
        torch.cuda.reset_peak_memory_stats(i)


def free(torch, *objs) -> None:
    for o in objs:
        del o
    gc.collect()
    torch.cuda.empty_cache()


def stage_load_forward_generate(name: str, report: dict, gen_tokens: int, min_devices: int) -> str:
    import torch
    from weaver_train import loader

    reset_peaks(torch)
    lm = loader.load(name, device_map="auto")
    report["model"] = lm.record
    report["load"] = {"path_from": "cache" if os.environ.get("WEAVER_TRAIN_MODEL_CACHE") and
                      str(lm.path).startswith(os.environ["WEAVER_TRAIN_MODEL_CACHE"]) else "store",
                      "devices": lm.devices, "pinned_layers": lm.pinned_layers,
                      "verify_s": round(lm.verify_seconds, 1),
                      "load_s": round(lm.load_seconds, 1),
                      "layers_per_device": _layers_per_device(lm.device_map), "memory": gpu_mem(torch)}
    if len(lm.devices) < min_devices:
        raise RuntimeError(f"model was not split across GPUs: {lm.devices}")
    first = next(lm.model.parameters()).device

    tok = lm.tokenizer
    ids = tok(PROMPT, return_tensors="pt").input_ids.to(first)
    t0 = time.monotonic()
    with torch.no_grad():
        logits = lm.model(ids).logits
    if not torch.isfinite(logits).all():
        raise RuntimeError("non-finite logits")
    report["forward"] = {"tokens": ids.shape[1], "seconds": round(time.monotonic() - t0, 2),
                         "logits_dtype": str(logits.dtype)}
    del logits

    msgs = [{"role": "user", "content": PROMPT}]
    chat = tok.apply_chat_template(msgs, add_generation_prompt=True, return_tensors="pt",
                                   return_dict=True).to(first)
    t0 = time.monotonic()
    with torch.no_grad():
        out = lm.model.generate(**chat, max_new_tokens=gen_tokens, do_sample=False)
    secs = time.monotonic() - t0
    new = out[0, chat["input_ids"].shape[1]:]
    report["generate"] = {"new_tokens": int(new.shape[0]), "seconds": round(secs, 1),
                          "tokens_per_s": round(new.shape[0] / secs, 2),
                          "text": tok.decode(new, skip_special_tokens=True),
                          "memory": gpu_mem(torch)}
    path = str(lm.path)
    free(torch, lm, out, chat, ids)
    return path


def _layers_per_device(device_map: dict) -> dict:
    counts: dict[str, int] = {}
    for k, v in device_map.items():
        if ".layers." in f".{k}.":
            counts[str(v)] = counts.get(str(v), 0) + 1
    return counts


def stage_lora(model_dir: str, report: dict, seq_len: int, targets: list[str], seed: int) -> None:
    import unsloth  # noqa: F401  (before transformers/peft)
    from unsloth import FastLanguageModel

    import torch
    from weaver_train import trainer

    for i in range(torch.cuda.device_count()):
        torch.cuda.empty_cache()
    reset_peaks(torch)
    t0 = time.monotonic()
    model, tok = FastLanguageModel.from_pretrained(
        model_name=model_dir, max_seq_length=seq_len, dtype=torch.bfloat16,
        load_in_4bit=False, load_in_8bit=False, full_finetuning=False,
        device_map="balanced", offload_embedding=False, trust_remote_code=False,
        local_files_only=True,
    )
    load_s = time.monotonic() - t0
    off_gpu = {str(p.device) for p in model.parameters() if p.device.type != "cuda"}
    if off_gpu:
        raise RuntimeError(f"parameters placed off-GPU: {off_gpu}")
    model = FastLanguageModel.get_peft_model(
        model, r=16, lora_alpha=32, lora_dropout=0.0, target_modules=targets, bias="none",
        use_gradient_checkpointing="unsloth", random_state=seed,
    )
    pinned = trainer.pin_layer_devices(model)  # the split-model race fix, as trainer.train applies it
    coverage = trainer.target_coverage(model, targets)
    trainer.check_coverage(coverage)
    devices = sorted({str(p.device) for p in model.parameters()})
    after_load = gpu_mem(torch)

    trainable = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(trainable, lr=1e-5)
    gen = torch.Generator().manual_seed(seed)
    vocab = model.get_input_embeddings().weight.shape[0]
    margin = min(100, vocab // 8)  # stay clear of special tokens at either end
    ids = torch.randint(margin, vocab - margin, (1, seq_len), generator=gen).to(
        model.get_input_embeddings().weight.device)

    model.train()
    reset_peaks(torch)
    t0 = time.monotonic()
    # Same autocast the HF Trainer applies with bf16=True (and so weaver_train.trainer);
    # Unsloth's compiled layers expect it.
    with torch.autocast("cuda", dtype=torch.bfloat16):
        out = model(input_ids=ids, labels=ids)
        loss = out.loss
    loss.backward()
    opt.step()
    opt.zero_grad(set_to_none=True)
    torch.cuda.synchronize()
    step_s = time.monotonic() - t0
    if not torch.isfinite(loss):
        raise RuntimeError(f"non-finite loss {loss.item()}")
    report["lora"] = {
        "seq_len": seq_len, "targets": targets, "r": 16, "coverage": coverage, "pinned_layers": pinned,
        "trainable_params": sum(p.numel() for p in trainable),
        "devices": devices, "load_s": round(load_s, 1), "step_s": round(step_s, 1),
        "loss": round(float(loss.item()), 4),
        "memory_after_load": after_load, "memory_step": gpu_mem(torch),
    }
    free(torch, model, opt, out, loss, ids)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name")
    ap.add_argument("--seq-len", type=int, default=32768)
    ap.add_argument("--gen-tokens", type=int, default=64)
    ap.add_argument("--targets", default="q_proj,k_proj,v_proj,o_proj")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--skip-lora", action="store_true")
    ap.add_argument("--min-devices", type=int, default=2,
                    help="GPUs the model must span after load (1 only for dry runs on a tiny model)")
    a = ap.parse_args(argv)

    import torch

    state = Path(os.environ.get("WEAVER_TRAIN_STATE_DIR") or REPO / "var")
    state = state if state.is_absolute() else REPO / state
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = state / "smoke" / f"{a.name}-{stamp}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"name": a.name, "started_at": stamp, "engine": "python-hf", "precision": "bf16",
              "python": platform.python_version(), "torch": torch.__version__,
              "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
              "gpus": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())],
              "ok": False}
    stage = "load"
    try:
        model_dir = stage_load_forward_generate(a.name, report, a.gen_tokens, a.min_devices)
        if not a.skip_lora:
            stage = "lora"
            stage_lora(model_dir, report, a.seq_len, a.targets.split(","), a.seed)
        report["ok"] = True
    except BaseException as e:  # noqa: BLE001 - record any failure, then stop
        report["failed_stage"] = stage
        report["error"] = f"{type(e).__name__}: {e}"
        report["traceback"] = traceback.format_exc(limit=8)
        if torch.cuda.is_available():
            report["memory_at_failure"] = gpu_mem(torch)
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "traceback"}, indent=2))
    print(f"report: {out_path}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
