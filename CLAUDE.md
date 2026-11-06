# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

`WeaverTrain/` (`weaver-train`) is a repository in the WeaverTools suite, created
2026-10-31. It will be integrated into WeaverTools later. Until then it is **standalone
Python and takes no dependency on any other WeaverTools repository**: no imports, path
couplings, shared fixtures or Cargo/Python linkage to `../WeaverAgent`,
`../WeaverAnalysis`, `../WeaverWeb` or `../WeaverTools`. The suite-wide conventions in
`../CLAUDE.md` (house style, seats, public-repository rules) still apply; its Rust
commands do not.

Goal: LoRA fine-tune `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16` (pinned in
`config/models.toml`) in bf16 and benchmark it untrained versus trained, for WeaverTools
loop work. The 2026-11-05 ruling adds dense Granite 4.2 30B as a separate rewriter series.
The retired Granite 4.0 hybrid stays retired. See `docs/decisions.md` and section 13 of
`docs/lora-rewriter-Spec.md`. Hardware: 2x RTX A6000, NVLinked, the model split across both.

**Training data is chosen by an evaluation the operator has laid out**
(`docs/decisions.md`): candidates come from NVIDIA's own sets
(`docs/nemotron-provenance.md`) and are filtered against the benchmark snapshots.
The first 2 STE100 batches use text from Gemini 4 Argon. The checker examines that text.
Do not use Gemini again. The next conversion uses the operator's model with the LoRA
adapter in stage 5 of the WeaverAgent conversion loop (`docs/decisions.md`).
Download only what `config/datasets.toml`
lists, and add an entry only on the operator's go. Do not request gated access.

## Commands

```bash
scripts/setup-env.sh                      # build + verify everything; re-runs skip done work
scripts/setup-env.sh --only verify --reverify   # re-run the kernel checks
scripts/setup-env.sh --only tests --retest      # re-run the unit suite
scripts/setup-env.sh --update-lock        # accept a deliberate dependency change

set -a; . ./.env; set +a                  # GPU selection etc.; tests need it
uv run --no-sync pytest -q                # full suite (GPU tests included)
uv run --no-sync pytest -q -m "not gpu and not multigpu"
uv run --no-sync pytest -q tests/test_bench.py::test_resume_skips_finished_tasks

uv run --no-sync python -m weaver_train.manifest verify MANIFEST DIR
uv run --no-sync python -m weaver_train.loader nemotron-3-nano-30b-a3b
scripts/setup-env.sh --only datasets     # fetch + verify config/datasets.toml entries
```

Use `uv run --no-sync` (or `.venv/bin/python`), not plain `uv run`: the mamba-ssm and
causal-conv1d wheels' filename versions disagree with their metadata, so every sync
reinstalls them. To change a dependency: edit `pyproject.toml`, `uv lock`, then
`scripts/setup-env.sh --update-lock --reverify`, and commit all three files.

## Architecture

- **Setup** (`scripts/`): `setup-env.sh` runs these stages in order: preflight, env
  (`uv.lock`), lock (`environment.lock`), verify (`verify_env.py`), models
  (`fetch_artifacts.py --set models`), benchmarks (`fetch_artifacts.py --set benchmarks`,
  `build_snapshot.py`), tests. Each stage writes a stamp under `$WEAVER_TRAIN_STATE_DIR`
  and skips when the stamp still matches. `docs/environment.md` explains every pin and
  stage. `environment.lock` fingerprints each installed build, which `pip freeze` cannot
  do; `uv.lock` stays the resolution source.
- **Primitives** (`src/weaver_train/`): `manifest`, `loader`, `trainer`, `bench`. **No
  module imports another except `manifest`** (enforced by `tests/test_independence.py`).
  `manifest` holds the two shared formats: the tree manifest (sha256 per file plus a
  `tree_hash`, used for models, adapters and benchmark snapshots) and the run record
  (`engine="python-hf"` is enforced). `loader` and `trainer` each verify the full model
  tree before loading. `bench` never loads a model: it calls a runner
  (`module:callable`) per task, so the Docker SWE harness plugs in as a runner.
- **Committed hashes, no paths:** `manifests/{models,sources,snapshots,datasets}/*.json`
  list files relative to the tree root, with the digest confirmed against Hugging Face's own (LFS sha256 or git blob
  sha1). Locations come only from `.env` (see `.env.example`). The data store also gets
  one whole-store manifest (`manifests/datasets/STORE.json`, copied into the store as
  `MANIFEST.json`). Retired artifacts' manifests move, unchanged, to `manifests/retired/`.
- **Tests** use a stand-in model built in `tests/conftest.py`: a tiny random NemotronH
  (layers Mamba, MoE, attention, Mamba) that runs the real Mamba kernels. Its Mamba
  conv width must stay a multiple of 8 (a causal-conv1d stride requirement).
  `conftest.SPLIT` puts a Mamba layer on the second GPU, where the split race happens.

## Known gaps (2026-10-31)

- **LoRA targets are decided:** `q/k/v/o_proj` plus Mamba `in_proj`, no expert
  adapters (`docs/decisions.md`). The trainer still refuses (`TargetCoverageError`) any
  run where a requested target exists but was not adapted.
- **Split models need `pin_layer_devices`.** One mamba-ssm Triton launch
  (`_chunk_scan_fwd_kernel`) has no device guard, so it runs on the current device's
  stream, which stays GPU 0 for layers on GPU 1. Without the hook,
  the model returns NaN logits. The loader and trainer apply it automatically; any
  new code path that runs a split model (e.g. a bench runner) must apply it too.
  Evidence: `docs/environment.md`, Known gaps.
- **PEFT refuses Mamba `out_proj` and `conv1d`** on Nemotron (`nemotron_h`); the
  attachable Mamba target is `in_proj`.
- **Smoke test (`scripts/smoke_test.py`):** Nemotron passes every stage, including
  the 32K LoRA step (peaks 33.8 / 36.1 GiB). Each stage runs in its own process because
  a resident model makes the next load offload to CPU.
- **MoE experts get no LoRA** (fused parameters, not `nn.Linear`); train runs record
  `component_coverage` per block type so this stays visible.
- Benchmark snapshots are frozen; no Docker harness exists yet.

## Rules that are easy to get wrong

- **Stop and report on any failure.** Do not improvise around kernel builds, CUDA/driver
  mismatches or model-support gaps (no swapping in a different kernel, falling back to a
  non-Mamba path, downgrading precision, or patching library code to get past an error).
  transformers and Unsloth both fall back to slow paths and only log a warning;
  `verify_env.py` exists to turn those fallbacks into failures.
- **Engine label is `python-hf`.** It is a separate tuple from any WeaverTools run, and
  results must never be pooled or compared as if from the same engine.
- **Reference weights are read-only** (the files are `chmod a-w`). Adapters go to a
  fresh `out_dir` outside the model tree. Re-verify against the manifest rather than
  trusting a directory listing.
- **GPU selection is by PCI bus:** `CUDA_DEVICE_ORDER=PCI_BUS_ID`, A6000s = 0,1. The box
  has a third GPU, and CUDA's default device order can change.
- **Large artifacts stay out of git**: weights, snapshots, images, run outputs. The
  repository is public: no box paths in committed files.
- Benchmark snapshots are frozen and dated; a changed snapshot is a new snapshot with a
  new hash, never an in-place edit.
