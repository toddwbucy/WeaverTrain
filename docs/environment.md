# Environment

How the weaver-train environment is built, why each version is pinned, and what
`scripts/setup-env.sh` guarantees. Established 2026-10-31.

## Guarantees

1. **Reproducible.** `uv.lock` pins the resolution, including a hash per artifact.
   `environment.lock` pins what was actually installed, down to the build: every line
   carries a hash of the package's installed files, so two builds of the same version
   (for example a CUDA 12.8 and a CUDA 13.0 xformers, both `0.0.35`) differ.
2. **Re-runnable.** Each stage records a stamp under `$WEAVER_TRAIN_STATE_DIR/stamps`
   when it succeeds, and skips when the stamp still matches. A re-run with nothing to
   do takes under a second.
3. **Verified, not assumed.** Kernels are run and compared with their reference
   implementations. Model files are compared with the publisher's own digests.
4. **Fails closed.** Any failed check stops the script. A reference model tree that is
   complete but does not match its manifest is reported, never repaired automatically.

## Stages

| Stage | Does | Skipped when |
|---|---|---|
| preflight | Checks the tools, `.env`, the driver's CUDA version (13 or later), and that GPU order is `PCI_BUS_ID` | never (cheap) |
| env | `uv sync --locked` into `.venv` | `uv.lock` and `pyproject.toml` are unchanged since the last successful sync |
| lock | Writes `environment.lock`, or checks the venv against it | never (the check is the skip test) |
| verify | `scripts/verify_env.py`: driver, GPUs, five kernel checks, Unsloth | it passed before for the same `environment.lock`, driver and GPU selection |
| models | `scripts/fetch_models.py`: archive store, then the fast-load copy | per model and per location: stamp matches, and every file's size and mtime is unchanged |

Options: `--only STAGE`, `--reverify`, `--rehash`, `--update-lock`, `--skip-cache`.

### Why the env stage does not use `uv sync --check`

The mamba-ssm and causal-conv1d release wheels have a local version in their filename
(`+cu13torch2.10cxx11abiTRUE`) that their own metadata lacks. uv therefore reports them
as outdated on every check and reinstalls them on every sync. The stage stamps
`uv.lock` instead, and the lock stage detects any real drift.

### What verify checks

| Check | How |
|---|---|
| driver, torch_cuda | Driver version; the expected count and model of visible GPUs; bf16 support; peer access between the two GPUs |
| causal_conv1d_kernel | Forward and backward pass, bf16, compared with `causal_conv1d_ref` |
| mamba_selective_scan_kernel | `selective_scan_fn` compared with `selective_scan_ref` |
| mamba2_ssd_kernel | Mamba-2 chunked scan (the path both models use) compared with `ssd_chunk_scan_combined_ref`; backward pass is finite |
| transformers_fast_path | Every kernel wrapper in `granitemoehybrid` and `nemotron_h` resolves to `causal_conv1d` or `mamba_ssm`, not to the PyTorch fallback |
| xformers_kernel | `memory_efficient_attention` compared with PyTorch SDPA |
| unsloth | Imports, and did not disable xformers at import |

Without these checks, transformers and Unsloth both fall back silently to slow PyTorch
paths and only log a warning. The report is written to `$WEAVER_TRAIN_STATE_DIR/env-report.json`.

## Pinned stack and why

| Component | Version | Reason |
|---|---|---|
| Python | 3.12 | Prebuilt mamba-ssm and causal-conv1d wheels are cp312 |
| torch | 2.10.0+cu130 | The newest torch with prebuilt mamba-ssm and causal-conv1d wheels for CUDA 13. Unsloth 2026.10.11 allows torch < 2.15, but no kernel wheels exist for 2.11 through 2.14 |
| mamba-ssm | 2.3.2.post1, wheel `cu13torch2.10cxx11abiTRUE` | From the GitHub release, since PyPI ships only an sdist. Avoids a local nvcc build (the host gcc is newer than nvcc officially supports) |
| causal-conv1d | 1.7.0, wheel `cu13torch2.10cxx11abiTRUE` | Same as mamba-ssm |
| xformers | 0.0.35 from the PyTorch cu130 index | The PyPI build targets CUDA 12.8; its extensions fail to load against cu130 torch, and Unsloth then switches to PyTorch attention without failing |
| unsloth, unsloth-zoo | 2026.10.11 | Current release on 2026-10-31. Caps transformers at 5.17.0 and trl at 1.15.0 |
| transformers | 5.17.0 | The highest version Unsloth allows. Has native `granitemoehybrid` and `nemotron_h`, so neither model needs `trust_remote_code` |

To change any pin, edit `pyproject.toml`, run `uv lock`, then
`scripts/setup-env.sh --update-lock --reverify`, and commit all three files.

## Configuration (`.env`)

| Variable | Meaning |
|---|---|
| `WEAVER_TRAIN_MODEL_STORE` | Archive location. Models are downloaded here and kept as permanent read-only references |
| `WEAVER_TRAIN_MODEL_CACHE` | Fast-load copy read by the loader and trainer. Empty means no copy is made |
| `WEAVER_TRAIN_STATE_DIR` | Stamps, environment report, run outputs. Not committed |
| `CUDA_DEVICE_ORDER`, `CUDA_VISIBLE_DEVICES` | GPU selection, by PCI bus order only |
| `WEAVER_TRAIN_EXPECT_GPUS`, `WEAVER_TRAIN_EXPECT_GPU_NAME` | What verify requires to be visible |
| `UNSLOTH_COMPILE_LOCATION` | Where Unsloth writes generated code (by default the current directory) |

`.env` is gitignored, and no path from it is committed. Manifests store only paths
relative to the tree root.

## Reference models

`config/models.toml` pins the repo id and revision. The first fetch hashes every file
and checks it against Hugging Face's own digests: the LFS sha256 for weights, the git
blob sha1 for small files. It then writes `manifests/models/<name>.json` with a sha256
per file, the source of that digest (`upstream`), and a `tree_hash`. The `tree_hash`
is the sha256 of the sorted `"<sha256>  <path>"` lines, and it is the model hash
written into run records. Verified files are made read-only. A new revision needs a
new manifest; the fetch refuses to verify against a manifest that pins a different
revision.
