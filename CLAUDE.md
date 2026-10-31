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

As of 2026-10-31 the tree holds only `LICENSE`. There is no build, lint or test setup
yet; add those commands here when Task 1 below lands them.

## Purpose and scope (week of 2026-10-31)

LoRA fine-tune two hybrid Mamba-attention MoE models in bf16, benchmark untrained versus
trained, and pick one to carry into WeaverTools loop work.

- Models: `ibm-granite/granite-4.0-h-small` and NVIDIA Nemotron 3 Nano 30B (**verify the
  exact Hugging Face repo id before downloading; do not guess it**).
- Hardware: 2x RTX A6000 (96 GB total). One model at a time, bf16, split across both
  cards.
- **Training data is not decided. Do not choose, build or download any training set.**
- Untrained baselines run later, with multiple repetitions each; not during setup.

### Setup tasks (the first session's scope)

1. Init the repo with uv or venv, pinned deps, and a README stating purpose and scope.
2. Install Unsloth plus `mamba-ssm` and `causal-conv1d`. Verify the kernels build
   against the installed CUDA and driver. Report versions.
3. Download both models in bf16, stored **outside git**. Record sha256 per file in a
   manifest. These bf16 copies are permanent reference artifacts and are never modified.
4. Smoke test each: load across both GPUs, one forward, one short generation, and a tiny
   LoRA step at 32K context to confirm memory fits. Report peak VRAM.
5. Benchmarks: SWE-rebench (or a similar rolling set) and the Multi-SWE-bench Rust split.
   Freeze a dated snapshot locally, preferring tasks created after both models'
   training cutoffs. Checksum the snapshot. Set up the Docker harness and run one task
   end to end.
6. Write a run manifest template: model hash, snapshot hash, seed, sampler config,
   precision, commit, engine.

## Rules that are easy to get wrong

- **Stop and report on any failure.** Do not improvise around kernel builds, CUDA/driver
  mismatches or model-support gaps (no swapping in a different kernel, falling back to a
  non-Mamba path, downgrading precision, or patching library code to get past an error).
- **Engine label is `python-hf`.** Every run manifest from this repository carries
  `engine = "python-hf"`. It is a separate tuple from any WeaverTools run and its results
  must never be pooled or compared as if from the same engine.
- **Reference weights are read-only.** Never write into the bf16 model directories; LoRA
  adapters and any merged weights go elsewhere. Re-verify against the sha256 manifest
  rather than trusting a directory listing.
- **Large artifacts stay out of git**: model weights, benchmark snapshots, Docker images,
  run outputs. The repository carries manifests (hashes), scripts and result notes.
  Locate stores through configuration or environment variables, not hard-coded paths,
  since the repository is public and must not name box paths.
- Benchmark snapshots are frozen and dated; a changed snapshot is a new snapshot with a
  new hash, never an in-place edit.
