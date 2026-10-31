# Decisions

Operator rulings that set this project's direction, newest first. Each entry gives the
date, the decision and its reasoning as stated.

## 2026-10-31: the data pipeline is the selection evaluation

The pipeline the operator laid out decides which training data is used: candidates
from NVIDIA's own sets are filtered against the benchmark snapshots
(`scripts/contamination.py`), rewritten to STE100 by the model itself, and checked. A
set enters training only through that evaluation.

Access and download limits, as ruled:

- `nvidia/Nemotron-SWE-v1` is downloaded first (done; 0 contamination hits against both
  snapshots).
- Gated pretraining sets: hold. Do not request access.
- The 2026 SFT sets (SFT-SWE-v2, SFT-SWE-v3, SFT-OpenCode-v1) are candidates:
  inventoried, not downloaded.
- Competitive-programming and math sets wait.
- Stop and ask before any download over 500 GB.
- The STE pilot stops after its first run and reports; no scale-up without the
  operator's go.

## 2026-10-31: vLLM in its own environment

The rewriting step serves the model with vLLM from a separate environment
(`vllm/`, own `pyproject.toml`, `uv.lock` and `.venv`), so vLLM's torch and
transformers pins never touch the training environment's verified builds.

## 2026-10-31: Nemotron 3 Nano 30B only; Granite dropped

weaver-train now targets `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16` only.
`ibm-granite/granite-4.0-h-small` is dropped from active configs and tests. Its bf16
weights and their manifest stay on disk, unchanged.

Reasoning:

- **Provenance.** NVIDIA publishes Nemotron's training data sources
  (`docs/nemotron-provenance.md`). That gives a basis for contamination checks and for
  judging what extra training the model needs.
- **Established methods only.** Standard LoRA on attention plus Mamba `in_proj`, the
  configuration the smoke test passed at 32K. No expert adapters and no
  `target_parameters` experiments.
- **Training data stays close to NVIDIA's own sets**, since the model was built on them.

This also settles the open Granite items: its Mamba LoRA coverage under Unsloth and its
32K memory limit no longer need a decision.

## 2026-10-31: the Rust snapshot is delta-only

`multi-swe-bench-rust-2026-10-31` predates the model's cutoffs, so its absolute scores
are not evidence of capability. Only the trained-minus-untrained difference is read,
since both runs share the same contamination (`docs/benchmarks.md`).

## 2026-10-31: per-layer device pin for split models

Split models get `pin_layer_devices`, a forward pre-hook per decoder layer, rather
than a library patch. It works around one unguarded mamba-ssm Triton launch
(`docs/environment.md`, Known gaps).
