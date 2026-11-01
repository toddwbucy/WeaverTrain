# Environment

How the weaver-train environment is built, why each version is pinned, and what
`scripts/setup-env.sh` guarantees. Established 2026-10-31.

## Guarantees

1. **Reproducible.** `uv.lock` pins the resolution, including a hash per artifact.
   `environment.lock` pins what was actually installed, down to the build: every line
   carries a hash of the package's installed files, so two builds of the same version
   (for example a CUDA 12.8 and a CUDA 13.0 xformers, both `0.0.35`) differ. The hash
   leaves out the `bin/` entry scripts the installer generates, since their shebangs
   hold the venv's absolute path and change when the venv moves.
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
| models | `scripts/fetch_artifacts.py --set models`: archive store, then the fast-load copy | per model and per location: stamp matches, and every file's size and mtime is unchanged |
| benchmarks | `fetch_artifacts.py --set benchmarks`, then `build_snapshot.py` | per source / snapshot: stamp matches and files unchanged; without a stamp a snapshot is rebuilt and must reproduce its committed hash |

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
| transformers_fast_path | Every kernel wrapper in `nemotron_h` resolves to `causal_conv1d` or `mamba_ssm`, not to the PyTorch fallback |
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

## Known gaps

**Unsloth's LoRA target filter skips Granite's Mamba blocks** (found 2026-10-31,
Unsloth 2026.10.11). `FastLanguageModel.get_peft_model` turns `target_modules` into a
regex that only accepts modules under path segments such as `self_attn`, `attention`,
`mixer` or `mlp`. On the real model structures, with targets
`q/k/v/o_proj, in_proj, out_proj`:

| Model | Mamba `in_proj` / `out_proj` adapted | Attention adapted |
|---|---|---|
| granite-4.0-h-small (`model.layers.N.mamba.*`) | 0 of 36 / 0 of 36 | 4 of 4 per projection |
| Nemotron-3-Nano (`model.layers.N.mixer.*`) | 23 of 23 / 23 of 23 | 6 of 6 per projection |

The trainer checks coverage after attaching LoRA and raises `TargetCoverageError`
before any training step if a requested target exists but was not adapted.
`tests/test_trainer.py::test_refuses_when_mamba_targets_are_dropped` pins the current
behaviour. Resolving it (an explicit target regex, plain PEFT for Granite, or an
upstream fix) is a methods decision that has not been made.

**fp32 adapter weights.** The LoRA A/B matrices are saved in fp32 while the base model
stays bf16. This is PEFT's default, not a precision change to the reference model.

**Multi-GPU race in the Mamba-2 Triton kernels: both models give non-finite output
when split across two GPUs** (found 2026-10-31; mamba-ssm 2.3.2.post1, triton 3.6.0,
torch 2.10.0+cu130, accelerate 1.15.0). Symptom: the first forward pass after a
`device_map="auto"` load returns all-NaN logits, for both Granite and Nemotron. The
model loads and splits fine (Granite 19/21 layers, Nemotron 27/25).

Root cause, established step by step:

1. `CUDA_LAUNCH_BLOCKING=1` makes the logits finite, so this is an ordering race, not
   bad weights or overflow (Granite activations peak near 560, well inside bf16).
2. Snapshots that do not synchronize put the first NaN in the second Mamba block on
   GPU 1 (Granite layer 20). Its input is finite; only its first 4 of 14 positions are.
3. While the layers on GPU 1 run, `torch.cuda.current_device()` is still 0. accelerate's
   layer dispatch does not change the current device.
4. Every Triton launch was logged with the current device next to its tensors'
   devices. mamba-ssm 2.3.2.post1 wraps its launches in
   `with torch.cuda.device(x.device.index)`, except one: `_chunk_scan_fwd` in
   `ops/triton/ssd_chunk_scan.py` launches `_chunk_scan_fwd_kernel` unguarded. It was
   the only launch that ran with GPU 1 tensors while GPU 0 was current (19 times, once
   per Mamba layer on GPU 1). Triton launches on the current device's stream; with peer
   access over NVLink, a GPU 0 stream can read GPU 1 memory, so the kernel runs
   unordered with GPU 1's own work. The launch is still unguarded on upstream `main`
   (checked 2026-10-31). causal-conv1d (a C++ extension with a device guard) and
   torch's `grouped_mm` were each tested and are not affected.
5. Setting the current device to each decoder layer's device in a forward pre-hook, with
   no added synchronization, makes both models' logits finite. Granite's logits then
   match the `CUDA_LAUNCH_BLOCKING=1` result: identical argmax at every position, max
   difference 0.375.

The unit test `test_splits_across_two_gpus` passed despite this: the race does not
show at stand-in sizes. That pass is not evidence that the split works.

Remedy (operator's decision, 2026-10-31): `pin_layer_devices`, kept identical in
`loader.py` and `trainer.py`. It adds a forward pre-hook per decoder layer that sets
the current device, applied automatically to any split model; the smoke test applies
it to the Unsloth path too. It patches no library code and adds no synchronization.
`tests/test_devices.py` checks the precondition directly: without the hook, the layer
on GPU 1 runs with GPU 0 current; with it, every layer runs with its own GPU current.
In a diagnostic process (no library files changed), wrapping only `_chunk_scan_fwd` in a
device guard, with no pin hook, made three forward passes finite on each model;
without it all three were NaN. Remove the hook once mamba-ssm guards that launch, and
keep the smoke test as the check.

Reported upstream 2026-10-31 as state-spaces/mamba#1072, with the fix as #1073 (one
guarded launch). The repro in the issue uses the real Nemotron config with random
weights, split 26/26: unguarded, 3 of 3 passes NaN; guarded, 3 of 3 finite and
bit-identical to a run that synchronizes both devices around the launch. A sync before
each decoder layer is not enough, since the unordered reads happen inside the Mamba
mixer.

**Importing Unsloth rewrites a mamba-ssm file on disk** (found 2026-10-31, unsloth-zoo
2026.10.11). `unsloth_zoo/temporary_patches/misc.py:fix_mamba_ssm_float32` rewrites the
installed `mamba_ssm/ops/triton/ssd_chunk_scan.py`, upcasting each `tl.dot` operand to
fp32, and replaces the file by atomic rename. No setting turns it off. It happens on the
first `import unsloth` (setup's verify stage), so every later run in the training
environment, Unsloth or not, uses the upcast kernel. `environment.lock` did not show
it, because it hashes RECORD entries, which keep the install-time hashes. The lock stage
now also runs `freeze_env.py --check-files`, which hashes every installed file against
its RECORD entry and fails on anything `config/known-modified-files.toml` does not list.
That file lists this one modification, by its content hash, plus two vLLM-environment
files whose wheel RECORD is wrong. A path shipped by two wheels (`build_backend.py`, from
flashinfer-python and torch_c_dlpack_ext) may match either owner. The multi-GPU repro
was re-run on the unmodified release file with Unsloth never imported; the result is
the same (state-spaces/mamba#1072, correction comment).

**PEFT refuses `out_proj` and `conv1d` on Nemotron's Mamba blocks** (found 2026-10-31,
peft 0.21.2): "Module 'out_proj' is incompatible with Mamba-based models
(model_type='nemotron_h'). Incompatible modules: {'conv1d', 'out_proj'}". PEFT raises
this before attaching anything. The Mamba projection PEFT accepts is `in_proj`. With
targets `q/k/v/o_proj, in_proj`, all 23 Mamba `in_proj` and all 6 of each attention
projection are adapted. Whether a run that adapts `in_proj` only is the intended Mamba
target set is part of the open targets decision.

**Granite's 32K LoRA step runs out of memory on GPU 0** (found 2026-10-31; seq 32768,
targets `q/k/v/o_proj`, r=16, Unsloth gradient checkpointing, one AdamW step). The
failing allocation is 5.00 GiB at `weighted_out[inv_perm]` in transformers'
`grouped_mm_experts_forward` (`integrations/moe.py`), reached through Unsloth's
compiled `GraniteMoeHybridMoE_forward`. At failure GPU 0 had 42.92 GiB allocated
(28.9 GiB after load), and GPU 1 was still at its post-load 31.11 GiB, so the
forward pass had not yet reached the GPU 1 layers.

The size follows from the routed-token buffer: tokens x experts per token x hidden x
4 bytes. It is fp32 because the fp32 routing weights promote the bf16 expert output.

| Model | Tokens x top-k x hidden | One routed buffer (fp32) |
|---|---|---|
| granite-4.0-h-small | 32768 x 10 x 4096 | 5.00 GiB |
| Nemotron-3-Nano | 32768 x 6 x 2688 | 1.97 GiB |

The expert path holds several such buffers at once, so Granite's MoE layer needs
roughly 2.5 times Nemotron's transient memory. Nemotron's step fits (peaks 33.8 and
36.1 GiB). After load, Granite leaves about 18.5 GiB free on GPU 0 and 16 GiB on
GPU 1. Moving layers between GPUs therefore probably does not free enough room on its
own; that is an estimate, not tested. Options include splitting the expert computation
into token chunks, a different memory placement, offload, or a shorter sequence. Each
changes the run or the code path, so none was tried.

At 16K (tried on the operator's suggestion, 2026-10-31) Granite's LoRA step passes:
56 s, peaks 41.9 GiB on GPU 0 and 44.0 GiB on GPU 1, so about 3.5 GiB of headroom at
batch size 1. The training length waits on the training data. If 32K turns out to be
required, splitting the expert computation into token chunks is the likely fix.

**Label shift, checked on real text.** The smoke step's loss on random ids (12.8 for
Nemotron, 13.3 for Granite) sits above ln(vocab), which is expected: a trained model
assigns confident probability to real continuations, so random targets score worse
than uniform. `check_label_shift` in the smoke test runs 512 tokens of ordinary text
through the same Unsloth forward with `labels=input_ids`. The loss is 2.95 for Granite
and 2.96 for Nemotron, against 11.52 and 11.78 for uniform, and the check fails the
stage above half of uniform. A loss scored without the shift would be far above that. Unsloth's
training forward returns no logits, so the stronger check (equality with the
cross-entropy of `logits[t]` against `ids[t+1]`) is coded but did not run.

**Smoke-test stages run in separate processes.** A model left resident in the parent
made the second load in the same process offload to CPU: Granite came up with `meta`
parameters and Nemotron failed with a weight "CONVERSION" error. `smoke_test.py` now
runs load/forward/generate and the LoRA step each in a fresh child process. The parent
creates no CUDA context, and each stage writes its own log next to the JSON report.

**Adapter coverage differs between the models, by block type** (measured 2026-10-31
by building each architecture on the meta device and attaching LoRA r=16 with plain
PEFT). Parameters are base weights excluding embeddings.

| Model, targets | Mamba layers adapted | Attention | MoE | Shared MLP |
|---|---|---|---|---|
| Granite, `q/k/v/o_proj` | 0 of 36 (11.6% of params) | 4 of 4 (0.5%) | 0 of 40 (85.5%) | 0 of 40 (2.4%) |
| Granite, plus `in_proj` (plain PEFT) | 36 of 36 | 4 of 4 | 0 of 40 | 0 of 40 |
| Nemotron, `q/k/v/o_proj, in_proj` | 23 of 23 (2.9%) | 6 of 6 (0.5%) | 0 of 23 (96.7%) | n/a |

Plain PEFT does attach `in_proj` to Granite's Mamba blocks; only Unsloth's target
filter drops it. No current target reaches the MoE experts in either model: they are
fused parameters, not `nn.Linear` modules. A difference between the two trained
models can therefore come from where they were adapted, not from the models. Every
train run records `component_coverage` (per block type: layers, adapted layers, base
and LoRA parameters) next to `lora_coverage` in `run.json`, and the smoke test
reports it too. Whether to match coverage across the models is a methods decision.
