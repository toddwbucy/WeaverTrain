# weaver-train

LoRA fine-tuning and benchmarking of hybrid Mamba-attention mixture-of-experts models,
in bf16, to choose one model to carry into WeaverTools loop work.

Part of the WeaverTools suite, to be integrated into it later. Until then it is
standalone Python and depends on no other WeaverTools repository.

## Scope

- Two candidate models, both bf16 and both pinned to a revision in
  `config/models.toml`: `ibm-granite/granite-4.0-h-small` and
  `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16`.
- One model at a time, split across two 48 GB GPUs.
- Benchmarks: a frozen, dated, checksummed snapshot of a rolling software-engineering
  task set, run untrained and trained, with repeated runs.
- Every run writes a record with engine `python-hf`. That is a separate tuple from any
  WeaverTools engine run, and the two are never pooled.

Out of scope for now: choosing training data, quantized training, any dependency on
other WeaverTools repositories.

## Setup

```bash
cp .env.example .env        # fill in the model store, cache and GPU selection
scripts/setup-env.sh        # safe to re-run; skips what is already done
```

The script builds the Python environment from `uv.lock`, checks it against
`environment.lock`, runs every CUDA kernel against its reference implementation, then
downloads and verifies the reference models. `docs/environment.md` explains each stage
and every pinned version.

## Layout

| Path | What |
|---|---|
| `scripts/setup-env.sh` | Environment build and verification (entry point) |
| `scripts/verify_env.py` | Kernel and stack checks, JSON report |
| `scripts/fetch_artifacts.py` | Pinned download (models, benchmark sources), upstream digest check, staging |
| `scripts/build_snapshot.py` | Freezes dated, reproducible benchmark snapshots |
| `scripts/smoke_test.py` | Per-model smoke test: load, forward, generate, 32K LoRA step, peak VRAM |
| `scripts/freeze_env.py` | Writes `environment.lock` (installed packages, per build) |
| `config/models.toml` | Reference models: repo id, revision, directory name |
| `config/benchmarks.toml`, `config/snapshots.toml` | Benchmark sources (pinned) and the snapshots frozen from them |
| `manifests/{models,sources,snapshots}/` | Per-file sha256 of every archived artifact (committed) |
| `src/weaver_train/` | Primitives: `manifest`, `loader`, `trainer`, `bench` |
| `uv.lock`, `environment.lock` | Resolution lock; installed-build lock |

## License

Apache-2.0.
