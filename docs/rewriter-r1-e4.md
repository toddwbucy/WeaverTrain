# R1-E4 procedure

This procedure implements Spec 0.4 REWRITE-R1-E4 and its score, report, review and expert clauses.
The operator authorized it on 2026-11-05.
Use the clean merged implementation commit for the new build, plans, training and scores.
Record all commands, UTC times, hashes and verification results in the local evidence and on issue 21.

The path variables below name local artifacts outside Git.
They do not name a new download or change an earlier deposit.
The original R1 build, plan, adapters, scores and reports stay unchanged.

Load the environment before each command.
Before each model procedure, repeat environment verification and the applicable REWRITE-MEMORY checks.
Only the approved graphics allocation and exact torchao warning are exceptions.
Stop on any other failure or fallback.

```bash
set -a
. ./.env
set +a
scripts/setup-env.sh --only verify --reverify
```

Build the new training record with the same approved pairs and prompt A.
The new command retains the R1 selection rule and renders identical training bytes.

```bash
uv run --no-sync python scripts/rewriter_build.py build \
  --pairs "$APPROVED_PAIRS" --folded-prompt "$PROMPT_A" \
  --selection r1-e4 --out "$E4_BUILD"
uv run --no-sync python scripts/rewriter_r1_e4.py plan \
  --build "$E4_BUILD" --original-build "$R1_BUILD" \
  --reference-plan "$R1_PLAN" --folded-prompt "$PROMPT_A" \
  --out "$E4_PLAN"
```

The experiment seal contains separate `heldout` and `training` plans and a copy of the original reference plan.
The held-out plan must match the original evaluation values, including every prompt token.
The training plan selects 50 source inputs with seed 2034 from the approved 200-pair order.
It retains the selected pair IDs, order hash, source text and code maps without target text.
Post the configuration, build, experiment and sample hashes before training.

Use a fresh process for training.
The wrapper calls the unchanged trainer with `config/rewriter-r1-e4.toml`.
It records import locations and hashes before and after training.
It verifies the clean producer commit, all four adapters and the complete loss and learning-rate records.

```bash
uv run --no-sync python scripts/rewriter_r1_e4.py train \
  --experiment "$E4_PLAN" --build "$E4_BUILD" --out "$E4_TRAIN"
```

Score each epoch once on each plan, in a fresh process after environment verification.
The example below is epoch 1 on the held-out plan.
Use epochs 1 through 4 and both `heldout` and `training` plans.
Give every invocation a fresh output directory.

```bash
uv run --no-sync python scripts/rewriter_score.py run \
  --plan "$E4_PLAN/heldout" --procedure r1-e4 --epoch 1 \
  --adapter "$E4_TRAIN/train/adapter-epoch-1" \
  --training-build "$E4_BUILD" --out "$E4_HELDOUT_1"
```

The 100-input scores and 50-input diagnostics have separate denominators and records.
An absent, empty or token-limit output remains a failure.
The score checks its producer and the pre-training plan before model loading and again after generation.
The score retains the first outputs for the fixed 30-source review sample.

After all four held-out scores, prepare their blind batch with `scripts/rewriter_review.py prepare`.
Supply the held-out plan and four `--candidate SCORE ADAPTER BUILD` arguments.
The review code rejects a missing or duplicate epoch, another procedure or another producer.
Stage only the pinned prompt and blind input in the fresh grader directory.
The review seat checks that directory, instructs the separate grader, checks its return and audits blindness.
It posts every grader exchange and the complete grader record on issue 21.
The executor then uses `ingest` with all three SHA256 arguments.
Do not generate another rewrite for this review.

The shared-expert inventory constructs only a meta model from the pinned configuration.
It lists full module names, dimensions and added rank-16 parameter counts.
It verifies the reference tree before and after inspection and attaches no adapters.

```bash
uv run --no-sync python scripts/rewriter_r1_e4.py experts --out "$E4_EXPERTS"
```

The final `report` command requires all eight scores, both R0 references, original R1 epoch 2 and the completed meaning review.
Use `--help` for the complete argument list.
The report rechecks score artifacts, replays meaning ingestion and joins losses and recorded rates by optimizer step.
It writes the public measurement note, a local JSON record and the full loss curve as CSV.
The public note contains measurements and hashes without source or rewrite text.
The JSON record and all large artifacts belong in the new deposit.
Post the report on issue 21, then stop.
R2, other target sets and later model series need their own authority and procedures.
