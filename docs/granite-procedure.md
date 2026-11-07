# Dense Granite procedure

Authority: section 13 of `docs/lora-rewriter-Spec.md`, approved in PR 43 on 2026-11-06.
The review seat must grade the code before a model procedure.
Use one clean merged checkout for the probe, build, plans, scores and training.
Keep the selected checkout unchanged while report PRs receive review.
The operator sets the schedule after the memory record is posted.

All commands require the loaded environment and this checkout's imports.

```bash
set -a
. ./.env
set +a
export PYTHONPATH="$PWD/src:$PWD/scripts"
```

Use the verified model store from `.env`, as the operator ruled on 2026-11-06.
A separate fast-load copy is optional.
The models stage checks publisher digests and writes `manifests/models/granite-4.2-30b.json`.
Check the selected reference tree against that full manifest before and after each model procedure.
The model procedures call fresh environment verification and retain its log and UTC times.
No command below retries, changes precision or reduces context after a failure.
Stop and post the failed record on issue 21. A retry needs a new operator ruling.
The context ruling of 2026-11-07 permits one new retry after its code and Spec amendment merge.
Keep both failed attempts unchanged and use the new clean merged commit for the remaining Granite sequence.
The probe records padding and end-of-turn IDs before and after loading.
Only the approved change from padding ID `100257` to `100261` is exempt.
End-of-turn IDs stay `100257`. All other stop rules remain in force.
The R1 extension on issue 21 applies the same exception and ID checks to the training load.
Review and merge its recording change before the pending memory retry, keeping the sequence on one merged commit.
R1 saves its before and after IDs in `train/run.json` before the first optimizer step.
Later score procedures check those IDs again.

Use 32,768 tokens throughout the Granite series, without truncation.
Before the PR is ready, report the largest held-out prompt plus reply budget in Granite tokens.
Stop before any retry if that sum exceeds 32,768.
The read-only audit on issue 21 found 16,233 tokens, and all 200 training sequences fit.
The memory check and R1 use the explicit device map in `config/rewriter-granite.toml`.
It moves layer 32 to GPU 0, for 33 layers on GPU 0 and 31 on GPU 1.
Embeddings remain on GPU 0. Final normalization, rotary embeddings and the output head remain on GPU 1.
Record and check the map and actual parameter devices. R1 must match the satisfactory memory check.
Scores retain balanced placement.

The path variables below identify local evidence outside git.
`PAIR_DIR` and `FOLDED_PROMPT` identify the approved pair tree and prompt A.
`NEMOTRON_BUILD` identifies the completed R1-E4 build used only to check source identities.
Every output directory must be new.

First, run only the memory check and post its time and GPU peaks on issue 21.

```bash
uv run --no-sync python scripts/rewriter_granite_probe.py --out "$GRANITE_PROBE"
```

After the operator sets the schedule, freeze the separate build and both plans.
The pair source fields must match Nemotron. The token counts and training bytes are new.
Publish the build, configuration and plan hashes before R0.

```bash
uv run --no-sync python scripts/rewriter_build.py build --pairs "$PAIR_DIR" --folded-prompt "$FOLDED_PROMPT" --selection granite-r1 --out "$GRANITE_BUILD"
uv run --no-sync python scripts/rewriter_granite.py plan --build "$GRANITE_BUILD" --reference-build "$NEMOTRON_BUILD" --memory "$GRANITE_PROBE" --folded-prompt "$FOLDED_PROMPT" --out "$GRANITE_PLAN"
uv run --no-sync python scripts/rewriter_score.py run --plan "$GRANITE_PLAN/heldout" --procedure granite-r0 --r0-pass 1 --out "$GRANITE_R0_FIRST"
uv run --no-sync python scripts/rewriter_score.py run --plan "$GRANITE_PLAN/heldout" --procedure granite-r0 --r0-pass 2 --out "$GRANITE_R0_SECOND"
uv run --no-sync python scripts/rewriter_review.py prepare --plan "$GRANITE_PLAN/heldout" --run "$GRANITE_R0_FIRST" --out "$GRANITE_R0_BATCH"
```

Post the batch identity and hashes on issue 21. Stage only its prompt and blind input for the grader.
The operator opens the grader. The review seat supplies instructions, checks blindness and records the exchange.
The executor must not start or instruct that session.
Use the existing review `ingest` command with the input, prompt and final verdict SHA256 arguments.
Keep the grader transcript, UTC times, invocation, corrections and hashes with the deposit.

Make the complete baseline report after ingestion, then publish it through a draft PR before training.

```bash
uv run --no-sync python scripts/rewriter_granite_report.py baseline --plan "$GRANITE_PLAN/heldout" --r0-first "$GRANITE_R0_FIRST" --r0-second "$GRANITE_R0_SECOND" --review-batch "$GRANITE_R0_BATCH" --review-result "$GRANITE_R0_REVIEW" --out "$GRANITE_BASELINE"
uv run --no-sync python scripts/rewriter_granite.py train --experiment "$GRANITE_PLAN" --build "$GRANITE_BUILD" --baseline "$GRANITE_BASELINE" --out "$GRANITE_TRAIN"
```

Score `train/adapter-epoch-1` through `train/adapter-epoch-4` with procedure `granite-r1` and the matching epoch.
Supply the Granite training build and use the `heldout` plan for each 100-source score.
Prepare one review batch with all four held-out score, adapter and build tuples.
Use the `training` plan for each 50-pair diagnostic. Keep the two score groups separate.
The review seat manages the new four-epoch grader session.
Ingest its return with all three hashes, preserving unavailable items without replacements.

The `rewriter_granite_report.py final` command takes the experiment, build, training procedure and baseline report.
It also takes both R0 scores, four ordered held-out scores, four ordered training scores and the review artifacts.
It writes the full loss and learning-rate curve, epoch means, score gaps, failures and paired comparisons.
Compare each epoch only with Granite R0 pass 1 under Granite's own decode margin.
Put the completed Nemotron and Granite results in a separate table with both model and procedure identities.
Do not pool baselines, infer a cause or recommend a next procedure.

Verify every completed tree and deposit manifests, README and SHA256SUMS under a new evidence identity.
Post starts, results and stops on issue 21. Keep its status pane current.
Publish the result note through a draft PR and stop. R2 and loop use are not authorized.
