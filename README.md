# weaver-train

LoRA fine-tuning and benchmarking of hybrid Mamba-attention mixture-of-experts models,
in bf16, to choose one model to carry into WeaverTools loop work.

Part of the WeaverTools suite, to be integrated into it later. Until then it is
standalone Python and depends on no other WeaverTools repository.

## Scope

- One model, bf16, pinned to a revision in `config/models.toml`:
  `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16`, split across two 48 GB GPUs.
  (`ibm-granite/granite-4.0-h-small` was dropped on 2026-10-31; see `docs/decisions.md`.)
- Training data: candidates from NVIDIA's own published sets
  (`docs/nemotron-provenance.md`), chosen by an evaluation: contamination filtering
  against the benchmark snapshots, an STE100 rewrite and a mechanical check.
- Benchmarks: frozen, dated, checksummed snapshots of rolling software-engineering task
  sets, run untrained and trained, with repeated runs.
- Every training and benchmark run writes a record with engine `python-hf`. That is a
  separate tuple from any WeaverTools engine run, and the two are never pooled.

Out of scope for now: quantized training, expert adapters.

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
| `config/models.toml` | Reference model: repo id, revision, directory name |
| `config/datasets.toml` | Candidate training data, pinned (downloaded on the operator's go) |
| `config/benchmarks.toml`, `config/snapshots.toml` | Benchmark sources (pinned) and the snapshots frozen from them |
| `manifests/{models,sources,snapshots,datasets}/` | Per-file sha256 of every archived artifact (committed); `retired/` keeps dropped ones unchanged |
| `docs/decisions.md` | Operator rulings and their reasons |
| `src/weaver_train/` | Primitives: `manifest`, `loader`, `trainer`, `bench` |
| `uv.lock`, `environment.lock` | Resolution lock; installed-build lock |

## License

Apache-2.0.
