# Decisions

Operator rulings that set this project's direction, newest first. Each entry gives the
date, the decision and its reasoning as stated.

## 2026-10-31: STE100 technical verbs, SQL verbs and boolean names (candidate)

Rulings for the candidate scheme file `ste/ste100-scheme.candidate.json`
(`ste100-scheme/1` shape). Every entry stays candidate and unratified. The base is
WeaverAgent's verb dictionary v0.6, which this repository reads and does not modify.

Technical verbs:

- **run**: provisional. Sense: to execute a program, a test, or a command. Condition:
  execute must not already be an approved STE100 verb. If it is, stop and report, and do
  not admit run.
- **commit**: admitted. One sense: to make a set of changes permanent in a repository or
  a database. It covers both Git and SQL, since Git is a store of changes.
- **merge**: to combine branches, or to combine changes into a store. Whether SQL MERGE
  is the same act under this sense is to be checked and reported.
- **push**: to send changes to a remote repository.

SQL verbs, candidate set: select, insert, update, delete, join, create, drop, alter,
rollback. Add grant and revoke only if permission work is in scope; ask first.

Boolean terms get no special handling. and, or, not keep their approved STE100
meanings in prose. Inside code they are operators and are not checked. Deferred to the
conditions grammar under O02-1.

Synonym check before anything is admitted. For each new verb, search the STE100
approved-word list (the operator's local copy, path in `.env` as
`WEAVER_TRAIN_STE100_SOURCE`, nothing committed) and the existing dictionary for a word
with the same meaning. Report each verb as one of:

1. no overlap: admit;
2. an exact approved match exists: use the approved word, do not admit;
3. a near-synonym or shared meaning: list it, and the operator rules.

Also flag any new verb that is itself an approved everyday word, and say whether its
approved meaning fits the SQL sense. Flag rollback against git revert.

Owed by the operator, not decided here: the sentence limit (20 for all emissions, or 20
for instructions and 25 for descriptions).

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
