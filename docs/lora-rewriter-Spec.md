# weaver-train Spec: LoRA for STE100 text conversion

**Status:** Granite clauses are approved in PR 43. Nemotron clauses are approved in PRs 26, 37 and 39.
PR 44 applies the operator's storage amendment of 2026-11-06.

**Version:** 0.5 **Date:** 2026-11-06 **Parent:** `docs/decisions.md`

**Language:** STE100. Use `ste/` at `ab63a918b569e5b8673dc2e78e66575ee9df1bd7`.
Add no words to that tree. Use `scripts/ste_check.py` with limits of 20 and 25 words.
Check `var/ste/approved-verbs.json` against its section 11 hash before the STE check.
Stop if the file is absent or its hash is different.
Clause names, file paths, keys and code spans are technical names.

## 0. Scope and authority

**REWRITE-SCOPE.** The first adapter rewrites one English text into STE100.
It uses the approved verbs and keeps the sense and data inside the placeholders.
It does not use tools. The later emitter adapter is a different task.
This follows the operator decisions of 2026-11-03 on [issue 17][ruling] and
[the reply about the prompt, pairs and balance][answers].
The [later layout decision][folded] gives the folded prompt authority.

`docs/lora-training-Spec.md`, version 0.3, stays the Spec of the emitter series.
Its data, adapters, reports and scores stay unchanged.
Do not compare its scores with the scores of this series.

**REWRITE-FILES.** This Spec gives approval for new code only after its merge.
The pair builder is `scripts/rewriter_build.py`.
The score command is `scripts/rewriter_score.py`.
The review command is `scripts/rewriter_review.py`.
The trial command is `scripts/rewriter_trial.py`, under section 12.
The R1-E4 command is `scripts/rewriter_r1_e4.py`, under section 4.
Tests and procedure configuration go in new files in `tests/` and `config/`.
Use the current trainer without changes to its code, except under GRANITE-FILES for the separate Granite series.
Each new piece of code cites the clause that gives it authority.

The reference freeze for issue 15 continues until that procedure is complete.
These files stay unchanged: `ste_convert.py`, `ste_check.py`, `ste_score.py`,
`ste_prompt.py` and `ste_kit_run.py` in `scripts/`.
The `ste/` tree, the 100-segment kit and `prompt-e21370fcd140.json` also stay unchanged.
The emitter code and Spec stay unchanged.
This document and the decision entry are the first change.
The pair code follows the merge of this document.

**REWRITE-LIMITS.** Use the pinned Nemotron 30B A3B model in bf16 on the two A6000s.
Section 13 gives the model and procedure rules for the separate Granite series.
The engine label is `python-hf`.
Keep the reference weights unchanged.
Use `pin_layer_devices` on every path that puts the model on two GPUs.
Stop and report any kernel, CUDA, model, memory or data-integrity failure.
Do not change libraries or procedure values to get past a failure.
Downloads must have the operator's approval and an entry in the current configuration.
Do not use Gemini again. Do not use the cancelled third batch.
No new text from a frontier model enters the training data.
Reviewer text is only an evaluation record.

## 1. The measurement

**REWRITE-MEASURE.** Measure the first rewrite from the model for each held-out text.
The primary result is the STE count of accepted results divided by 100.
Use the same inputs and checker for the base model and each adapter.
Record the review of the sense in a different record.
A satisfactory checker result does not show that the rewrite keeps the sense.
The correct-request rate does not apply because this task has no tools.
Do not make a SWE skill claim from this procedure.

## 2. Pairs from the stored batches

**PAIR-INPUTS.** Read only the 387 accepted trajectories from batches `200` and `200-2`.
Their hashes are in section 11.
Check each conversion file against its `run.json` before use.
Read the source segments, code maps, part inputs and round outputs from the same batch.
Check the source and code-map files against their hashes in section 11.
Record each input file's sha256 and the source dataset revision.
Reject duplicate batch and segment IDs.
Reject a dataset row that occurs in both batches.
Stop on a file hash failure or an ambiguous source record.

**PAIR-HELDOUT.** Read the source files of the 100-segment and 500-segment kits.
Their hashes are in section 11.
Compare trajectory UUIDs, not dataset row numbers.
Stop if a trajectory from the conversion has a UUID from either kit.
No segment from that trajectory can enter either training set.

**PAIR-TARGET.** Make one pair for each accepted narration or final-report segment.
Do not use issue text, `think` fields or tool output.
Use `ste_convert.split_ws` to divide the source body from its outer whitespace.
Use the unchanged `ste_convert.mask` on that body.
Its text with placeholders and code map must equal the stored input and code map.
Keep the outer whitespace in the record.

Read round outputs in numeric sequence from 1 through 4.
For each ID, select the last stored output that assembly used.
Do not select an earlier output to get past a check that is not satisfactory.
The selected output must satisfy the batch checker, converter checks and literal check.
Before `Judge` reads the lexicon, check `var/ste/approved-verbs.json` against section
11.
Stop if the file is absent or its hash is different.
Use `ste_experiment.Judge` and `altered_literals` with the pinned prompt.
Put back its code spans with the source map and its outer whitespace.
The result must equal the segment from the conversion exactly.
Report and remove each pair that does not satisfy these checks.
Do not repair its text.

All 13,369 targets in these batches keep their text with placeholders.
Use those stored targets without another rewrite or a new application of the masker to
the target.
Each pair holds the original input with placeholders and the selected target with
placeholders.
Record the batch, segment ID, trajectory UUID, dataset row, locator and round of the
target.
Also record the code map, outer whitespace, source hash and target hash.
The checker accepted the targets. The targets did not have a review of the sense.
Record that limit in each data record.

**PAIR-REPAIRS.** Keep repair conversations in a different file from the pairs for the
first output.
Keep their original request, previous output, checker notes, result and round identity.
Do not replace a repair conversation with a new conversation that has different text.
The stored set covers the 1,490 selected targets from rounds 2 through 4.
Keep references to all earlier outputs for those targets.
Whether R1 also uses these conversations stays open under section 10.
No repair conversation enters training without that decision and its Spec change.

**PAIR-PROVENANCE.** Record three source prompt identities in different fields.
The grouped instruction made the batch targets.
The single-text bundle holds the system text and four examples.
The system text has its own hash.
Section 11 gives all three values.
The grouped instruction has 47,412 characters.
The single-text system message has 45,692 characters.
Do not claim that the targets came from the single-text request layout.

**PAIR-BALANCE.** Use character counts of the source with placeholders for the three
bins.
Their bounds are below 200, 200 through 499, and 500 or more.
Use weights 37, 30 and 33 in that sequence.
For a set of N pairs, calculate `N * weight / 100` for each bin.
First use the integer part of each result.
Give the other positions to the bins with the largest fractional parts.
For equal fractional parts, use the bin sequence above.
This is the rule of `ste_kit_sample.targets`.

Use seed 0 for the pair sequence.
For each pair, hash the UTF-8 bytes of `0\n<batch>\n<segment_id>` with SHA256.
There is no final LF in those bytes.
Sort each bin by that hash, then by batch and segment ID for equal hashes.
Use the first required pairs from each bin without replacement.
Sort the selected pairs by the same key for the output sequence.
Record the IDs in sequence and their hash.
For this hash, use a JSON list of `[batch, segment_id]` pairs in the output sequence.
Use UTF-8, `ensure_ascii=True`, `separators=(",", ":")` and no final LF.

The largest set whose bin counts fit the accepted pool has 3,383 pairs.
Its bin counts are 1,252, 1,015 and 1,116.
The approved R1 set has 200 pairs, with counts 74, 60 and 66.
Every R1 pair is in the larger set.
If a check decreases the pool, calculate the counts again and report the change.
Do not fill a bin with pairs from another bin or with repeated pairs.
The operator must give approval for changed procedure counts before a procedure.

**PAIR-OUTPUT.** The `pairs` command writes a new output directory.
It holds `pairs.jsonl`, `repairs.jsonl`, `excluded.jsonl` and `pairs-record.json`.
The pair record holds the input hashes, checks, counts, bins and prompt identities.
It also holds the provenance and limits of PAIR-TARGET and PAIR-REPAIRS.
Make a tree manifest with `weaver_train.manifest`.
Do not overwrite a current directory.
These files stay outside git.

## 3. The prompt and token labels

**RENDER-LAYOUT.** The `build` command reads a verified pair directory.
Use the folded prompt from RENDER-SEED.
Each training sequence has these messages in sequence:

1. One system message with the full rules and all four examples inside its text.
2. One user message with the original input with placeholders.
3. One assistant message with the selected target.

The user content is `"<<<TEXT\n" + masked_input + "\nTEXT>>>"`.
Keep the folded file's bytes exactly. Keep all examples inside the system message.
Keep the user wrapper's LF bytes exactly.
Supply no `tools` argument to the template.
Set `enable_thinking=false`.
Use the model's `apply_chat_template` without a new template or a shorter prompt.
The operator accepted the cost of the full prompt.

**RENDER-SEED.** Use the folded prompt file named by WeaverAgent's workflow PR as a
data input.
That workflow gives the exact fold, example sequence and markers.
The operator must review the folded file before the seed.
Record its SHA256 as `folded_prompt` in section 11 before a training data record or R0.
Import no code from WeaverAgent.

Before you make a training data record, check the folded file against its section 11
hash.
After this check, use the file bytes for the training data record, R0 and R1.
The loop's seeded save point is not required for those procedures.
This file check does not show which bytes the loop uses.
The seed check is required before loop use under REWRITE-RECONCILE.
The folded prompt is the render identity.
Keep its hash and the three source hashes of PAIR-PROVENANCE in different fields.
Also record the complete initial message-list hash in a different field.
For the source bundle hash, use `json.dumps([system_text, examples]).encode()` as the
reference does.
For message-list hashes, use UTF-8 JSON with `ensure_ascii=True`.
Set `separators=(",", ":")` and use no final LF.
Each message has `role` then `content`, with no other field.
If the file hash or layout is different, stop and request an operator decision.
The absence of a seed record does not stop data preparation, R0 or R1.
Follow the procedure sequence and decisions in REWRITE-ORDER.

**RENDER-MASK.** Give loss to the final target, its end-of-turn token and the LF after
that token.
The template adds that LF.
System tokens, with all examples, and input and template-header tokens have a mask
value of 0.
Apply the template to the first two messages with `add_generation_prompt=True`.
Apply it again with the final target and no generation prompt.
The first token sequence must be an exact prefix of the second.
Set `assistant_masks` to 1 only for the tokens that the second sequence adds.
Stop if the prefix check is not satisfactory.
All tokens in the first sequence have a mask value of 0.

**RENDER-LENGTH.** Keep `max_seq_length=49152` for this series.
Do not cut a pair or its prompt.
Stop and report if a selected pair has more tokens than this limit.
The operator must give a decision on any length change before a new data record.

**RENDER-OUTPUT.** Write `train.jsonl`, `build.json` and a tree manifest to a new
directory.
Each training row holds `input_ids`, `assistant_masks` and its pair identity.
The data record holds the pair manifest, selection, prompt identities and counts.
Record all tokens and final-target tokens in different fields for each epoch.
Record the selected IDs, seed, bin counts and removed pairs.
Keep these five RECONCILE-FIELDS names and meanings:

- `tree_hash`: the verified model tree.
- `template_hash`: the model template hash.
- `tools`: `false`, because the template receives no tool list.
- `enable_thinking`: `false`.
- `max_seq_length`: `49152`.

The prompt identities are different record fields, not new RECONCILE-FIELDS.
Post any change to these five values on the new run-log issue.
The emitter's current data records stay unchanged.

## 4. The trainer

**REWRITE-TRAIN.** Use the current trainer with `data.format="tokenized"`.
The clauses below from Spec 0.3 also apply here:

- TRAIN-TOKENIZED and TRAIN-MASK-REACHES-LOSS.
- TRAIN-NO-PACKING and TRAIN-EPOCHS.
- TRAIN-EPOCH-ADAPTERS, TRAIN-RECORD and TRAIN-STOP.

Use the mask for the final target from RENDER-MASK.
All tokens with mask 0 have label -100 at the loss.
All tokens with mask 1 have their input token ID as the label.
Each target token has the same weight.
Do not add weights for long text or for a particular rule.

**REWRITE-CONFIG.** The procedure values follow Spec 0.3 RUN-CONFIG.
Use LoRA rank 16, alpha 32 and dropout 0.0.
Targets are `q_proj`, `k_proj`, `v_proj`, `o_proj` and Mamba `in_proj`.
Do not attach adapters to MoE experts.
Use learning rate `1e-4`, a cosine schedule and 10 warmup steps.
Use batch size 1, gradient accumulation 8 and seed 0.
Use two epochs unless REWRITE-R1-E4 applies.
Keep Unsloth `gradient_checkpointing="unsloth"` and the verified placement on the two
GPUs.
Do not put pairs together in one sequence.
Start each training procedure from the base model with a new adapter.
Save and score the adapter from each epoch.

**REWRITE-R1-E4.** The [operator decision of 2026-11-05][r1-four] gives approval for one new procedure named R1-E4.
Use four epochs with the same 200 pairs, their sequence, seed 0 and prompt A as the completed R1.
Prompt A is the approved folded prompt in section 11.
Use the `r1_ordered_ids` pin from `config/rewriter-inputs.toml`.
Keep repair conversations outside training.
Start from the base model with a new adapter under REWRITE-CONFIG.
Use a copy of `config/rewriter-train.toml` named `config/rewriter-r1-e4.toml`.
Change only `train.num_train_epochs` from 2 to 4.
All other training values stay unchanged.
The cosine schedule uses the four-epoch step count from the start.
Keep the earlier R1 records, adapters and report unchanged.

Use one clean merged main commit for the new training data record, plan, training and scores.
Make a new training data record and plan at that commit under REWRITE-RECORD.
Check the selected IDs, token counts and `train.jsonl` hash against the completed R1 training data record.
Stop if any value is different.
Record the new training data, plan and configuration hashes before training.
Save an adapter at the end of each epoch and check it against its manifest.

Before each model procedure, apply REWRITE-MEMORY and do environment verification again.
Only the approved graphics allocation and exact torchao warning are exceptions.
Other failures and fallback warnings still stop the procedure.
Before training or generation, check that repository modules come from the clean procedure checkout.
Record `weaver_train.__file__` and the SHA256 of each imported repository module in the local record.
Check the import locations and file hashes again after each procedure.
Each new procedure record must name the clean launch commit.
Check the training producer commit before the first score and after each score.
A dirty, absent or different commit stops the procedure.
Keep the trainer code unchanged.

**REWRITE-R1-E4-SCORES.** Score each epoch adapter on the frozen 100-segment plan as for R1.
Use REWRITE-DECODE and REWRITE-CHECK without repairs or output selection.
Keep the same input IDs, text with placeholders, code maps, token IDs and prompt bytes.
Keep the same model, environment, template, checker and decode values.
Record any different value and stop before generation.
Each new plan has its own clean commit, configuration and training data record.
Each saved score must satisfy the checks against its own plan, configuration and producer.
These record identities can be different, but the frozen evaluation inputs and values must be equal.

For each epoch, report accepted results out of 100, by kind and by each source-length bin.
Report failures by rule, placeholder failures, empty outputs and token-limit outputs.
Compare each epoch against the first R0 procedure and the original R1 epoch 2 under unchanged RUN-MARGIN.
Use the recorded R0 decode difference for both comparisons.
Keep the old producer identity and its recorded correction unchanged.
The old discrepancy gives no exception for a new R1-E4 producer.
Keep each epoch's outputs for the fixed 30-source sample from seed 2031.
Review their sense under REWRITE-R1-E4-REVIEW after all four held-out scores are complete.

**REWRITE-R1-E4-REVIEW.** The [operator decision][r1-four-review] gives approval for a blind review of all four R1-E4 epochs.
Use each epoch's saved first outputs for the fixed 30-source sample from seed 2031.
Do not make a new rewrite for this review.
Apply REWRITE-REVIEW without changes to its rules.
Keep one result for each procedure and epoch.
Check placeholders and literals before code spans are put back.
Keep unavailable items in the record without replacements.
Use the same four verdicts and calculate defect rates only on reviewed items that the checker accepted.

Use the unchanged `config/ste-rewrite-review-prompt.md`.
Its SHA256 is `c18b9dea736f81fea6225d1e096e54a1d6d8840805085fb542c62b4a32f9e112`.
Use the [approved grader procedure][r1-four-grader] in a different `claude-opus-5-5` session with fresh context.
Its new directory initially holds only `prompt.md` and `input.jsonl`.
Keep the procedure map and other records outside that directory.

The [operator's division of work][r1-four-review-seats] gives batch preparation and the `ingest` command to the executor.
The review seat checks the prepared grader directory and its hashes.
The review seat gives the grader its instruction and any format correction directly.
It checks the verdict file before its hash is recorded and audits the transcript for blindness.
It records each message between the review seat and grader on issue 21.
It posts the grader record there for the executor's `ingest` command.
That record holds the model ID, invocation, UTC start and end times, transcript and all corrections.
It also holds the prompt, input and verdict file hashes.
The executor uses `--input-sha256`, `--prompt-sha256` and `--verdicts-sha256` for ingest under REWRITE-REVIEW.

Extend the review code to accept epochs 1 through 4 for R1-E4.
Check all four distinct epochs and their saved score and producer identities.
Reject an absent, duplicate or different epoch and a different procedure or producer.
Keep the checks for earlier procedures unchanged.
Keep this batch and its verdicts different from the earlier R1 batch.
Report the verdict counts for each epoch beside its STE scores, with the reviewed denominator and defect rates.
Report numbers only, with no cause label or recommendation.

**REWRITE-R1-E4-TRAIN-SCORES.** The [operator amendment][r1-four-loss] adds one frozen sample of 50 training pairs.
Start with the approved 200 pairs in their PAIR-BALANCE sequence.
Use `random.Random(2034).sample` to select 50 pairs without replacement.
Keep the returned sequence for all four epoch adapters.
Before training, freeze the selected `[batch, segment_id]` values and their hash under the PAIR-BALANCE JSON rule.
Freeze each source input, its code map and the same prompt and decode values in a different plan.
Use the original source with placeholders as the request, as in RENDER-LAYOUT.
Do not give the target text to the model.
Use REWRITE-DECODE and the frozen checker, with a denominator of 50 for this sample.
An absent, empty or token-limit output is a failure out of 50.
Keep the four procedures and their records different from the 100-segment scores.
For each epoch, report the training-sample STE rate beside its held-out STE rate.
The gap is the training-sample rate minus the held-out rate, in percentage points.
These training pairs are not an independent evaluation set.

**REWRITE-R1-E4-REPORT.** Keep the full per-step loss curve and the recorded learning rate for each step.
Use the current trainer's records without a new learning-rate calculation.
For each epoch, report the arithmetic mean of its logged optimizer-step losses and the quantity of those steps.
Put each loss and learning-rate record together by step identity.
Stop on an absent, duplicate or non-finite value.
Report all four held-out scores, training-sample scores and paired comparisons.
Include the review counts under REWRITE-R1-E4-REVIEW before the final report.
Report wall time and peak memory on each GPU for training and each score procedure.
Report the numbers without a cause label or recommendation.
The learning rate decreases to zero under the cosine schedule.
A flat final loss does not identify a data or capacity limit.
Use a new deposit and result note under REWRITE-RECORD, and post each event on issue 21.
Stop after the report. R2 and loop use are outside this approval.

**REWRITE-R1-E4-EXPERTS.** Examine the pinned 30B model without changes to its files or training.
For each MoE layer, list the full module names and matrix dimensions of the shared expert's MLP.
For each linear module, calculate the extra LoRA parameters at rank 16 as `16 * (in_features + out_features)`.
Report the count for each module, each layer and the complete model.
Do not count a parameter twice or include current targets in the added count.
Keep the model, configuration and implementation hashes with the list.
Check the reference tree against its manifest before and after this examination.
This count gives no approval for a change to the LoRA targets or another training test.

## 5. Procedure sequence and cost

**REWRITE-ORDER.** Open a different run-log issue for the rewriter series.
Record the merged Spec, code, frozen inputs and procedure values before R0.
The operator gave approval for the sizes in section 10 on 2026-11-03.
Changed sizes must have approval before their dependent procedure.
The decision on repair data follows the complete R0 report, as section 10 tells.
Do not use the emitter's R0 or R1 as a rewriter baseline.

1. R0 uses the untrained 30B model on the frozen 100-segment rewrite kit, twice.
2. The first R1 uses 200 pairs for two epochs, then stops.
   REWRITE-R1-E4 gives approval for one new four-epoch R1 procedure before R2.
3. R2 uses 3,383 pairs for two epochs, only on the operator's instruction.

Complete R0 and its review of the sense before R1.
The operator then gives a decision on the open cell for repair data.
Report R1 wall time, maximum memory on each GPU, the complete loss curve and each epoch score.
The executor stops after that report and does not start R2.
There is no R3 in this series.

**REWRITE-MEMORY.** Keep the satisfactory 49,152-token RUN-MEMORY record as the current
reference.
It used the real model and the training placement on both A6000s.
Record its identity with the new procedure plan.
If the environment, model, placement or training path changes, do that check again
before R1.
Stop on failure and report it. Do not select another kernel or precision.
No model is loaded for the cost analysis below.

**REWRITE-COST.** The [analysis of 2026-11-03][cost] used the pinned tokenizer and
the approved folded file.
It did not read a seed record or load model weights.
These counts replace the costs of the earlier ten-message layout.

- All accepted pairs: 13,369 pairs and 194,537,118 tokens for one epoch.
- R1: 200 pairs and 2,938,741 tokens for one epoch.
- R2: 3,383 pairs and 49,727,659 tokens for one epoch.

The full pool has 722,550 loss tokens per epoch.
R1 has 25,637 loss tokens per epoch. R2 has 443,682 loss tokens per epoch.
These counts include the target, end-of-turn token and final LF.
The full pool has sequence lengths from 14,455 through 16,035 tokens.
R1 sequence lengths range from 14,466 through 15,411 tokens.
R2 sequence lengths range from 14,456 through 16,035 tokens.
These totals do not count pad tokens or the different repair conversations.
The [review seat][cost-review] measured the R1 costs again and got the same result.
Record new totals if the selected pairs or training policy change.
Do not turn these token counts into a measured wall time.

## 6. Scores and review of the sense

**REWRITE-KIT.** Use the same frozen 100-segment source kit as issue 15.
It has 90 narration segments and 10 final reports, with source seed 2030.
Its length mix after the mask is 37, 30 and 33.
Check both kit hashes in section 11 for the exclusion check.
Make the score input with the unchanged masker and keep each code map.
Keep one frozen input file and message plan for both R0 procedures and all adapters.
The score denominator is 100, with missing outputs.
Do not select prompts from the emitter's 80-entry score plan.

**REWRITE-DECODE.** The new score script loads the model through the current loader.
Check the full model tree and adapter manifest before use.
Use the template, prompt identities and five fields of the new data record.
For R0, use the same frozen values with no adapter.
Decode greedily with one beam and `do_sample=False`.
Use the reference reply budget `min(8192, len(masked_input) // 2 + 256)`.
Check prompt length plus reply budget against `max_seq_length` before any generation.
Stop if one prompt does not fit.
Record the first output only. Do not repair it or select a later output.
Keep the raw tokens, text, finish reason and wall time.

**REWRITE-CHECK.** Score the first outputs with the frozen `scripts/ste_score.py`.
Check `var/ste/approved-verbs.json` against section 11 before the scorer reads the
lexicon.
Stop if the file is absent or its hash is different.
Supply both `--input` and `--prompt` explicitly on every use and rescore.
Use `prompt-e21370fcd140.json` for `--prompt`.
This bundle supplies the checker limits and example checks.
RENDER-SEED supplies the system bytes for generation.
Use the model text after the same removal of outer whitespace as `ste_kit_run.py`.
Do not change the text or its case to increase a score.
An absent output or an output stopped by the token limit is a failure.
An empty output, or an output with only whitespace, is an absent output.
Keep its raw output in the record and do not write its ID to the scorer's output file.
The scorer then records that ID as missing in its denominator of 100.
Reject duplicate or unknown output IDs before the scorer reads them.
Record the checker result and converter checks for every ID.
Record each failure to keep a placeholder or a literal from the source.
Do not change the frozen checker or add a correct-request metric.

**REWRITE-SPREAD.** Do R0 twice with identical inputs, values and model files.
Record both rates for the first output and their absolute difference.
Use the first R0 procedure as the reference for later paired comparisons.
Apply Spec 0.3 RUN-MARGIN only to the rewrite STE result.
Keep its sign test, interval, five-point range and decode-difference rule unchanged.
Report the quantity of accepted results by kind and by the three source bins.
Record output lengths and failures by rule with each result.
Do not claim an increase in the quality of the sense from the STE rate alone.

**REWRITE-REVIEW.** Use `claude-opus-5-5` and `config/ste-rewrite-review-prompt.md` from
issue 15.
Its exact hash is in section 11.
The file must merge with that plan before review of the sense starts.
Select 30 source IDs with seed 2031 before R0.
Use `random.Random(2031).sample` on the source IDs in alphabetic sequence.
Keep the same IDs for every procedure.
Put available first rewrites from the compared procedures in one sequence.
Use one result for each procedure and epoch.
For R0, use only the first result.
First sort by procedure, epoch and source ID.
Use `random.Random(2032).shuffle` and give the results opaque review IDs.
Keep the procedure map outside the reviewer input.
Record the exact sequence and its hash.
Each review batch gets a new identity and keeps the earlier verdicts.
Before you map verdicts, check the prompt, input and verdict file hashes against
the deposit.

The reviewer reads the original source and the rewrite with its code spans put back.
Put code spans back only with that source's frozen map.
Keep the text with placeholders and the code maps in the deposit for audit.
Do not put code spans back into a rewrite that did not satisfy the placeholder or
literal check.
Report missing and unreviewed items without a substitute sample or an invented verdict.
The four verdicts are `faithful`, `minor drift`, `wrong` and `data altered`.
Report all four counts and the reviewed denominator.
Report distinct defects per 100 reviewed segments that the checker accepted.
Also report the count and rate of segments with defects.
A zero denominator gives an undefined rate.
Keep the original verdict beside any operator override, with the operator and absolute
date.
No review text or verdict enters training data.

## 7. Records

**REWRITE-RECORD.** Keep the new series different from the emitter series and issue 15.
Each score record holds the model, adapter, environment, kit, prompt and template
identities.
It holds the five data fields, all limits, output hashes and the result for every ID.
It also holds wall time, GPU memory, accepted totals, review of the sense and
exclusions.
A procedure with a failure records its completed IDs and its failure.
Do not replace the record of the failure with a successful record.
Record whether the seed check and joint token check are complete.
Keep their later results in a different record.

Use a new deposit in `weaver-testing/` with a README and `SHA256SUMS`.
Keep inputs, accepted pairs, repair records, plans, outputs, adapters and logs outside
git.
The repository report contains measurements, limits and hashes only.
Post its link and deposit identity on the rewriter run-log issue.
Do not commit machine paths, host names, source text or prompt text from the standard.

## 8. The joint token check

**REWRITE-RECONCILE.** The loop on a second machine must use RENDER-LAYOUT exactly.
Its initial messages and each repair request must have authority from WeaverAgent's
merged workflow.
This Spec gives that repository no new authority.
Keep the current RECONCILE-FIELDS names and meanings from Spec 0.3.
The rewriter data record supplies the values for this adapter.

Before loop use, get the seed record from WeaverAgent issue 449.
Check the SHA256 that the seed calculated from the inserted prompt bytes.
It must equal the `folded_prompt` hash in the training data record and section 11.
Do not use a hash copied only from the certificate.
Record the seed artifact identity, save point identity and digest, certificate digest
and operator review reference.
Equal hashes show that the prompt bytes are equal.
No export of the prompt bytes is required.
Stop loop use if the record is absent, incomplete or different.
Do not change a training data record or its prompt to make the hashes equal.
If this check is not satisfactory, keep its record and request an operator decision.

Use one held-out input with placeholders to write one complete initial message list.
Both sides use those same message-list bytes.
Compare the full token ID sequences from Python and the SPU.
Record the folded prompt hash, three source prompt hashes and message-list hash in
different fields.
Apply Spec 0.3 RECONCILE-ADAPTER, RECONCILE-DRIFT and RECONCILE-RECORD.
Do not operate the loop with this adapter before the joint checks are satisfactory.
If a field or prompt identity changes, do the affected checks again and post the change.
Token equality does not measure rewrite quality.
The R0 and R1 scores and reviews of the sense show that result.

## 9. Tests

Each new test cites its clause. GPU tests use the small model of `tests/conftest.py`.

- **PAIR-INPUTS, PAIR-HELDOUT:** Reject bad hashes, duplicate identities and a kit UUID
  on a different dataset row.
- **PAIR-TARGET:** Check the source map, round of the target, equality after code spans
  are put back, checker checks and literal identity.
  A last output that is not satisfactory must not select an earlier accepted output.
  A missing or changed approved-verbs file must stop the procedure before `Judge` reads
  it.
- **PAIR-REPAIRS, PAIR-PROVENANCE:** Keep repair history in a different file and record
  all three source prompt identities without interchange.
- **PAIR-BALANCE:** Check exact quotas, equal-fraction sequence, seed repeatability, no
  replacement and the nested R1/R2 selections.
  The present pool gives 3,383 pairs. A reduced pool cannot fill an incomplete bin from
another bin.
- **RENDER-LAYOUT, RENDER-SEED:** Check the folded system message, user wrapper,
  LF bytes, tool absence and absence of separate example messages.
  Check the folded prompt hash and its three source prompt hashes without interchange.
  Missing or different folded file bytes stop the training data record.
  The absence of a seed record does not stop the `build` command with the approved
  folded file.
- **RENDER-MASK:** Compare the final-target token span with the mask.
  Only the target, end-of-turn token and template's final LF have a mask value of 1.
  The system, with all examples, the user input and the final assistant header have
  a mask value of 0.
  A broken prefix check must stop the data record.
- **RENDER-LENGTH, RENDER-OUTPUT:** Reject a pair beyond the limit and a changed
  manifest.
  Check all five field names and the different prompt identities.
- **REWRITE-TRAIN:** On the GPU, record the labels at the loss for a pair with the
  folded system prompt.
  Only the target, end-of-turn token and template's final LF have labels other than
  -100.
  All examples are system text and have label -100.
  Examine epoch adapters and the current stop rules with the small model.
- **REWRITE-KIT, REWRITE-DECODE:** Both base and adapter paths use the same 100 inputs
  and fit checks.
- **REWRITE-CHECK:** Missing and truncated outputs stay failures out of 100.
  Empty outputs and outputs with only whitespace must count as missing.
  Reject duplicate and unknown IDs. Compare results with the unchanged scorer.
  A missing or changed approved-verbs file must stop the procedure before the scorer
  reads it.
- **REWRITE-SPREAD:** Check paired comparisons against the first R0 procedure and the
  recorded decode difference.
- **REWRITE-REVIEW:** Check the prompt, input and verdict hashes, blind sequence,
  code spans put back, missing cases, defect denominators and override records.
- **REWRITE-RECORD:** A procedure with a failure keeps its records and cannot overwrite
  a completed directory.
- **REWRITE-R1-E4:** Check four saved epoch adapters with the small model.
  Check the unchanged R1 selection, training tokens and all training values except the epoch count.
  Reject repository imports from another checkout and changed module hashes.
  Reject a new record with an absent, dirty or different launch commit.
  For each new rejection check, remove that check for a mutation test.
  With that check absent, the test must not be satisfactory.
  Each rejection test checks the specific reason, with other checks satisfactory.
- **REWRITE-R1-E4-SCORES:** Check all four epochs against both frozen references and unchanged RUN-MARGIN.
  Reject a changed input, prompt, checker, model, environment or decode value between plans.
  A different plan commit must not hide a change to an evaluation input.
- **REWRITE-R1-E4-TRAIN-SCORES:** Check the seed, 50 distinct pairs, selection hash and unchanged sequence across epochs.
  Keep target text outside generation requests and check the denominator of 50.
  Check missing, empty and token-limit failures, and the reported gap in percentage points.
- **REWRITE-R1-E4-REPORT:** Check step identities, complete loss and learning-rate records, the steps in each epoch and arithmetic means.
  Reject absent, duplicate and non-finite values with specific reasons.
- **REWRITE-R1-E4-EXPERTS:** Check full module names, dimensions, rank-16 counts and exclusion of current targets.
- **REWRITE-R1-E4-REVIEW:** Check batch preparation and the `ingest` command for all four R1-E4 epochs.
  Check the fixed sample, saved first outputs, blind sequence, prompt pin and all three hashes for the `ingest` command.
  Check placeholder and literal checks before restoration, unavailable items, four verdict counts and checker-accepted defect denominators.
  Reject an absent, duplicate or different epoch and a different procedure or producer.
  Keep the earlier procedure checks and verdicts unchanged.
  For each new rejection check, remove that check for a mutation test.
  With that check absent, the test must not be satisfactory.
  Each rejection test checks the specific reason, with other checks satisfactory.
- **REWRITE-TRIAL-INPUTS, REWRITE-TRIAL-PLAN:** Check whole-trajectory exclusions, source hashes, snapshot checks, seed repeatability and plan hashes.
  A different row number must not hide a forbidden UUID.
  Check all four contamination keys against each frozen snapshot.
  Reject changed plans, code maps, counts, text with placeholders and source identities before model loading.
  Check the exact plan bytes.
- **REWRITE-TRIAL-RUN, REWRITE-TRIAL-RECORD:** Check both paths with the same frozen inputs and the pinned epoch 2 adapter.
  Check device pins, full tree checks, first outputs, the reply budget and the denominator of 20.
  Reject repository imports from another checkout and new records with a dirty or different launch commit.
  Check missing outputs, token limits, placeholder checks, literal checks and unchanged scorer results.
  For each new rejection check, remove that check for a mutation test.
  With that check absent, the test must not be satisfactory.
  Each rejection test checks the specific reason, with other checks satisfactory.
- **REWRITE-RECONCILE:** Record the two real token sequences and compare them in the
  joint check.
  Stop loop use if the seed record is absent, incomplete or has a different prompt hash.
  Check the seed record and the file hash in different steps.
  A unit test does not replace that measurement.

The current independence tests continue to apply.
Do the full suite with GPU tests before every push.
Use the pinned STE checker on this document and record its result in the PR.

## 10. Decisions and open cells

**Full prompt and layout.** Closed by the operator on 2026-11-03.
Use the full production prompt and the folded layout under RENDER-LAYOUT.
The later decision replaces only the earlier layout decision.
Do not use a shorter prompt for cost.

**Pair function and balance.** Closed by the same decision.
Use original input with placeholders to final accepted target and the 37/30/33 mix.
PAIR-BALANCE gives seed 0 and the exact integer counts.

**Procedure sizes.** Approved by the [operator decision of 2026-11-03][approvals].
The first R1 uses 200 pairs and two epochs. R2 uses 3,383 pairs and two epochs.
Both use seed 0 and the costs in section 5.
The [decision of 2026-11-05][r1-four] gives approval for R1-E4 under section 4.
The size approval does not start R2.
The operator must give the instruction for R2 after the R1 report.

**Repair training in R1.** Closed for R1 by the [operator decision of 2026-11-05][r1-go].
R1 does not train on repair conversations.
The decision on repair training moves to R2.
R1 can start after the R0 report is on main.
Start R2 only on the operator's later instruction.
Use repair conversations only after a Spec change for masks, counts and costs.

[r1-go]: https://github.com/toddwbucy/WeaverTrain/issues/21#issuecomment-6658527439
[r1-four]: https://github.com/toddwbucy/WeaverTrain/issues/21#issuecomment-6668124199
[r1-four-loss]: https://github.com/toddwbucy/WeaverTrain/issues/21#issuecomment-6668140899
[r1-four-review]: https://github.com/toddwbucy/WeaverTrain/pull/39#issuecomment-6668437190
[r1-four-review-seats]: https://github.com/toddwbucy/WeaverTrain/pull/39#issuecomment-6668462707
[r1-four-grader]: https://github.com/toddwbucy/WeaverTrain/issues/21#issuecomment-6646130753

**Loop capture and conversion.** Open for the joint work with WeaverAgent.
The operator reviewed the folded file on 2026-11-03.
The seed record work is in [WeaverAgent issue 449][seed-record].
The record reader waits for that work.
The `build` command, R0 and R1 use the approved file under RENDER-SEED.
The seed record and the joint token check are required before loop use.
No organ amendment is required for this layout.
REWRITE-RECONCILE closes this cell with records.

## 11. Pinned identities and analysis

The SHA256 values below identify the current inputs.
The operator [approved the folded file][prompt-review] on 2026-11-03.
Its source is [WeaverAgent PR 448][workflow] at
`80bc9bf30a277f86b7e1ebc565102c874ee64919`.
The file is `docs/crates/weaver-harness/Loops/ste-conversion-prompt.txt`.
It has 47,214 UTF-8 bytes, with the final LF.
The `folded_prompt` value is a data pin. It adds no code dependency.
Do not commit the file in this repository.
Do not use a stand-in fold or a source provenance hash for that value.
Different file bytes must have a new operator review.

```text
folded_prompt
0fe2ee35a59f6b1e30734af4d7a72e32bd6dbc0e728abc98e32e2c609dad07ed
approved_verbs
c622bc230bc0c29512ef16990f527bf32a5ffaffbe955f73751251a7e5ab4693
converted_200
63afa6fe3aa1f44cb98355e6717b073921c2ee1ce1451a93acf0aae20e349ad9
converted_200_2
9bd8bac719156498c4f2ac7916b0afae85aa0ac909ad9cece95b5fe48fbe23a5
source_200
de201c0f342be8a12ff376b81b87d1e8078e8bb8dc874a56b3fc1a6d4f9c890e
codes_200
a8e943555ec6e3f8a871287379915de4aaa3eb75131545b7e5ffa825cdab7b35
source_200_2
4214931200cefdd05977c0d0cbfcb69376f205d4da07c423fe955d6fd4b7f5b4
codes_200_2
98b2c2b1e62f8fb7b43c78853ccc6ffcdd83bb781457ee663bcdf6e4b69960ba
kit_100_source
d2f4937aeef40a7b178faf19ba1790e578d2b397e965f7289d929d1c6c9c4dcd
kit_500_source
1e80e935c0008e0ca44dd9af633f3f0c29678f1799d0cb62d816102ca4ff489c
grouped_instruction
ab4d2d8a87a434f976a19ea7b470e14f1fb37125f61b4188521ff0cd0fb26d2f
single_text_bundle
e21370fcd140a4bb5e7a0d2dd415c58d8ecead9333a9e730adb66ab96e7a851c
system_text
0a42709ebe47ef8f17d728921b8bd802e0bf1466fced8d020b0b3c6dec6aa701
meaning_review_prompt
c18b9dea736f81fea6225d1e096e54a1d6d8840805085fb542c62b4a32f9e112
```

The [pair audit][audit] used reference commit
`d4aa1b5156b4caa6464aa040542940a1c0d793c8`.
It found 13,369 accepted pairs with no UUID overlap with either kit.
Their counts in the source bins are 10,401, 1,852 and 1,116.
The [folded-cost analysis][cost] used pairs from
`0ba99aa53237e40afde34cd26eda10365bff59a5`.
Its script and result stay outside `scripts/`.
The pair counts and ordered-ID hashes stay the same with the folded layout.
The hashes below identify the folded-cost analysis and the selected pair sequences.

```text
analysis_script
c17ad9b14392601471b8d1d9292e82d6f8fb83b0f7cc6533879aa455e333f5f2
analysis_result
40fc851c99de2cbbb39fe62655e8935a736b2806b304a7ce44270cea7296db20
r1_ordered_ids
9c8afa63abc7d4aa9652a4dfcb04f66fadfe97c81dc29a37f094f8782c2371d2
r2_ordered_ids
8cde3c1cbd5c8cb5c8cb288d24fe9d9f038fece32e2983f81c9edada380c9a21
```

## 12. Trial on new conversion segments

**REWRITE-TRIAL.** The [operator instruction for issue 36][trial] gives authority for this trial after this clause merges.
Use 20 new segments with the base model and the R1 epoch 2 adapter.
This trial does not change REWRITE-MEASURE, REWRITE-KIT or REWRITE-SPREAD.
Its denominator is 20. It does not replace the frozen 100-segment scores.
It gives no verdict on the sense and no approval for loop use.
The review batch `2847c781` still holds the review of the sense.
REWRITE-RECONCILE, the seed record and deferred item E still gate loop use.
No training, R2, repair, harness work or new download is authorized.
No trial output enters training data.

**REWRITE-TRIAL-INPUTS.** Use `nemotron-swe-v1` at its revision in `config/datasets.toml`.
Check its full tree against the committed manifest before selection.
Use only narration and final reports under PAIR-TARGET.
Do not use any trajectory from batches `200` and `200-2`, with or without accepted targets.
Do not use any trajectory from the 100-segment or 500-segment kits.
Check the four source hashes in section 11 before the exclusion check.
Compare UUIDs, as in PAIR-HELDOUT. Do not compare only row numbers or segment locators.
Reject ambiguous UUIDs or locators.
Check each candidate's whole trajectory with `scripts/contamination.py` against both frozen snapshots in `config/snapshots.toml`.
Check both snapshot trees against their committed manifests first.
Remove a trajectory if any of the four contamination keys matches either snapshot.
Record each exclusion reason and the dataset and snapshot identities.
Use the unchanged masker and `split_ws` from PAIR-TARGET.
Keep the raw source, outer whitespace, text with placeholders, code map, UUID and locator.
Use only sources that contain prose outside their placeholders.

**REWRITE-TRIAL-PLAN.** Select one segment per trajectory with seed 2033.
Use the PAIR-BALANCE length bins and weights. The counts are 7, 6 and 7.
Select two final reports and 18 narration segments.
For each candidate, hash a JSON list of `[2033, uuid, locator]` with SHA256.
Use UTF-8, `ensure_ascii=True`, `sort_keys=True`, `separators=(",", ":")` and no final LF.
Sort candidates by this hash, then by the same JSON bytes for equal hashes.
First select two final reports in that sequence, within the bin counts and with different UUIDs.
Then select narration segments in that sequence to fill each bin, without a repeated UUID.
Sort the selected segments by the same key for the output sequence.
Stop if the eligible pool cannot fill these counts. Do not change the seed or counts to continue.

Freeze the segment list, exact selection method and seed in a plan before generation.
Keep their hashes, source file hashes, exclusion record and all procedure values in that plan.
Record the model, adapter, producer record, environment, prompt, template, checker and snapshot identities.
Record the original R1 training data commit and producer commit in different fields.
The trial commit has its own field.
Check the training data commit against the saved R1 plan.
The trial plan must have the new merged trial commit.
If the producer and training data commits are different, get the operator's decision before model use.
The operator [approved this saved R1 adapter][trial-ruling] with the provenance correction in [PR 38][trial-correction].
Record that decision with the original R1 identities in the trial plan.
Do not change a stored training data record or producer record to give it the trial commit.
Keep each source ID, bin, kind, locator, code map and outer whitespace in the frozen input.
Check every prompt and reply budget against the unchanged context limit before generation.
Stop if any selected segment does not fit. Do not select a substitute.
Use a clean, merged commit for the plan and both model procedures.
Record that full commit and a tree manifest for the plan.
Check the plan and frozen input again before each procedure.
Write `plan.json` as UTF-8 JSON with `ensure_ascii=True`, `sort_keys=True` and `separators=(",", ":")`.
Add one final LF. Calculate SHA256 over those exact file bytes.
The plan holds the selected IDs in sequence, method, seed, exclusions, input identities, procedure values, commits and tree identities.
For each frozen input file, record its relative name, byte count and SHA256 in the plan.
Post the file digest as `plan_sha256` on issue 36 before the first generation.
Post the manifest's `tree_hash` as `plan_tree_hash` in a different field.
The review seat grades the plan and code, with mutation tests, before generation.

**REWRITE-TRIAL-RUN.** Do the base procedure once, then the R1 epoch 2 adapter procedure once, on the same 20 inputs.
Use REWRITE-LIMITS, RENDER-SEED and REWRITE-DECODE, with the unchanged loader and `pin_layer_devices`.
Load each repository module from the run checkout.
Keep `weaver_train.__file__` and the SHA256 of each imported repository module in the local procedure record.
Before generation, check that the new record's commit is equal to the clean launch commit.
Stop if the commits are different or either checkout is dirty.
Use the epoch 2 adapter tree below from the completed R1 deposit.

```text
05c86cd2d18faa5480836140c36a62f524fc418c6d7f24f27b53dc218e88d030
```

Check the adapter manifest and producer record before and after its procedure.
Check the full reference tree before and after each model procedure.
Keep the reference weights and both saved R1 epoch adapters unchanged.
Use the approved folded prompt, greedy decode, code spans with placeholders and reference reply budget.
Record one first output per segment, with no repair, retry or output selection.

Before each model procedure, use `scripts/setup-env.sh --only verify --reverify` again.
Record its commit, UTC time and result on issue 36.
Only the approved 4 MiB graphics allocation on each selected GPU is permitted before the procedure.
Only the exact torchao warning from the decision of 2026-11-04 is exempt.
Keep that warning in the log if it occurs.
Stop and report any other failure or fallback. Do not change libraries, kernels, precision or procedure values.
Keep the failed record and completed outputs. A retry must have a new operator instruction.

**REWRITE-TRIAL-RECORD.** Apply REWRITE-CHECK to each first output, with a denominator of 20 for this trial.
Use the frozen checker with explicit input and prompt files.
Keep missing and token-limit outputs as failures.
Record every checker rule, placeholder check and literal check for each source ID.
Put code spans back only when both the placeholder and literal checks are satisfactory.
Keep other outputs with placeholders and mark them as unrestored.
Keep raw tokens, raw text, finish reasons and output hashes in the deposit.

Keep a run record and tree manifest for each procedure.
The plan must also have a run record and tree manifest.
Deposit the records under REWRITE-RECORD with a README and `SHA256SUMS`.
Post each procedure start, result or stop on issue 36.
Keep the issue body current with the status and links.
The deposit shows each source, base rewrite and adapter rewrite side by side.
Show each output's checker result and whether its code spans were put back in that deposit.
Keep all source and rewrite text outside git under REWRITE-RECORD.
The result note in `reports/` contains measurements, limits and hashes only.
Name the dataset, its revision and the deposit in the note.
The [operator decision][trial-ruling] gives authority for this layout.
No license-notice retrieval or new download is authorized.
Keep the frozen selection and outputs unchanged.
Report counts by source bin and kind, failures by rule, empty outputs and outputs stopped by the token limit.
Report wall times and peak memory on each GPU for both procedures.
Do not pool these results with another engine or the frozen scores.
The review seat grades the result note. A checker result is not a verdict on the sense.

## 13. Dense Granite rewriter

**GRANITE-SCOPE.** The [operator decision of 2026-11-05][granite-series] opens a separate dense rewriter series after Nemotron R1-E4.
The [target decision of 2026-11-06][granite-targets] selects the full attention and MLP target set at rank 16.
Compare the completed rewriters on the same task, pairs, prompt, checker and source samples.
Keep different procedure names, identities, records and results for each model.
The model, earlier training, tokenizer, template and adapter size can be different.
Do not give architecture alone as the cause of a difference.
Nemotron stays on the cutover path. The retired Granite 4.0 hybrid stays retired.
No R2, shared-expert training, smaller model, quantized model, new dataset or loop procedure has approval here.

Section 13 supplies the Granite exceptions to REWRITE-LIMITS, REWRITE-CONFIG, REWRITE-ORDER and REWRITE-MEMORY.
Its rules for model identity, tokens and references replace the Nemotron identities for this series only.
All other rules for pairs, token labels, checker, review and records still apply.
Keep all Nemotron procedures, deposits, adapters and reports unchanged.
The trial on issue 36 is a different task with its own approval.

**GRANITE-FILES.** This section gives authority for dependent code only after its merge.
The review seat must grade the code before any Granite model procedure.
Use model configuration fields in the current builder, scorer, review and report commands where possible.
Add Granite procedure files, configuration and tests where the current commands cannot express its records.
Each new behavior cites its clause. Keep the public commands and records for Nemotron compatible.
Loader and trainer changes have authority only for Granite support, target checks, placement and procedure records.
Keep the current loss, optimizer, schedule and label behavior unchanged.
Do not import another repository or change its files.
The files frozen by REWRITE-FILES stay frozen.

**GRANITE-MODEL.** Use `ibm-granite/granite-4.2-30b` at `9e668ce1c538387ef24d3644e9b0606647762636` in bf16.
Use model key `granite-4.2-30b` and directory name `ibm-granite--granite-4.2-30b` in `config/models.toml`.
Check the download against publisher file digests at that revision through the models stage.
Write its manifest in `manifests/models/` and make reference files read-only.
Load from the verified bulk store under the [storage decision of 2026-11-06][granite-store].
A separate fast-load copy is optional. Check the selected copy against the full manifest before use.
Check full reference trees before and after every model procedure.
Do not use the review seat's file-size check as a hash check.
Use `GraniteForCausalLM`, with `model_type="granite"`, and the installed native implementation.
Do not use remote model code.
Keep `engine="python-hf"` and the model identity in every record.
Keep results from other engines in different records.

Read the pinned model card and its data disclosure before setup.
Use the [model card at the pinned revision][granite-card] and [IBM's data disclosure][granite-disclosure].
Record their revisions and hashes, and the limits of their source metadata.
The disclosure lists public and nonpublic sources and synthetic data.
It does not show that the held-out sources were absent from the model's earlier training.
Do not add a dataset or a new training target from that disclosure.

**GRANITE-VERIFY.** Load `.env` before every procedure and use `uv run --no-sync`.
Use `CUDA_DEVICE_ORDER=PCI_BUS_ID` and the two GPUs selected by `.env`.
Use `scripts/setup-env.sh --only verify --reverify` before each model procedure.
Record its UTC times, commit and result before the procedure starts.
Add checks for the dense native and Unsloth paths with a small Granite model.
Record the actual attention implementation and device placement.
Use `pin_layer_devices` on every path that puts the model on two GPUs.
Model weights must stay in bf16 on the selected GPUs, with no CPU or disk offload.
Keep the approved Unsloth checkpoint policy. It is not permission for weight offload.

Keep the approved graphics allocation and exact torchao warning as exceptions.
Under the [decision of 2026-11-07][granite-padding], do one new memory check after the first stop.
The exact Unsloth warning about `pad_token` is also an exception.
The change is from `<|im_end|>`, ID `100257`, to `<|fim_pad|>`, ID `100261`.
Before model loading, record the tokenizer, model and generation `pad_token_id` and `eos_token_id` values in the probe record.
All six IDs must be `100257`. Record the actual IDs again after Unsloth loads the model.
All three `pad_token_id` values must then be `100261`. All three `eos_token_id` values must stay `100257`.
Check both sets of IDs before the optimizer step and when a later procedure reads the probe record.
Keep the captured IDs on failure. Keep the first stop record unchanged.
Training labels still come only from `assistant_masks`, with one intact sequence per batch.
Scores still use the native loader without Unsloth.
Review and merge the record change and this amendment before the retry.
Use the new clean merged commit for the retry and all later Granite procedures.
The permission to do R1-E4 verification again does not apply to Granite.
Stop and report every other failure or fallback, with the failed record kept unchanged.
Do not change libraries, kernels, precision, context length or procedure values to continue.
The operator must give a new instruction before a retry.

**GRANITE-TARGETS.** Use LoRA rank 16, alpha 32 and dropout 0.0.
Target `q_proj`, `k_proj`, `v_proj`, `o_proj`, `gate_proj`, `up_proj` and `down_proj` in every decoder layer.
Do not adapt embeddings or the output head.
Keep bias and other parameter training off.
Record every target module's full name and matrix dimensions before adapters are attached.
Check every intended module after attachment and reject a missing adapter or a trainable parameter outside this set.
For a target with no adapter, stop with `TargetCoverageError`.
Record the actual trainable parameter count and component coverage.
For each linear module, rank 16 adds `16 * (in_features + out_features)` parameters.
The pinned dimensions give 448 modules in 64 layers and 140,509,184 trainable parameters.
Check these counts against the adapted model before the memory check and training.
Stop on a difference. Do not change targets to get Nemotron's parameter count.

**GRANITE-DATA.** Use the same 200 accepted pairs and PAIR-BALANCE sequence as Nemotron R1-E4.
Keep every batch ID, segment ID, source, target, code map and outer whitespace unchanged.
Apply PAIR-INPUTS, PAIR-HELDOUT and PAIR-TARGET to the verified source records.
The ordered-ID SHA256 must equal `r1_ordered_ids` in section 11.
Keep the source-bin counts 74, 60 and 66. No repair conversation enters training.
Use prompt A with the exact `folded_prompt` bytes from section 11.
Keep all four examples in the system text and the same user wrapper as RENDER-LAYOUT.
Use no tools and set `enable_thinking=False` for both training and scores.

Use Granite's pinned tokenizer and its unchanged chat template.
The `chat_template.jinja` SHA256 is `f0ba43f79b3cabca5e5a7584c77aec92f49feae45cdf7779c87d9fc54cd90258`.
The `tokenizer_config.json` SHA256 is `2007f26df166ed10a2d6fa881c1c8f3cdd151e451469f59b274897f14449835f`.
Check these bytes against the manifest before use.
Freeze the template hash, message-list hashes, token IDs and stop-token ID in a new Granite plan.
The assistant header with `enable_thinking=False` has the empty `<think></think>` markers.
This header is part of the prompt prefix.
Its exact suffix is `"<|im_start|>assistant\n<think></think>"`, with no final LF.
The end-of-turn token is `<|im_end|>`, ID `100257`.
The complete assistant message ends with `"<|im_end|>\n"`.
Check the token ID against the tokenizer and its configuration before generation.
Give those header tokens no loss. This prefix is outside the model's new output tokens.
Check the exact prefix relation required by RENDER-MASK with this template.
Give loss only to the added final target, end-of-turn token and final LF.
Stop if the prefix, stop token or final LF does not agree with the pinned template.
Do not repair the layout or remove `<think>` text from new outputs.
Do not change the template to continue.

Keep `max_seq_length=49152`, with no truncation or shorter prompt.
Count tokens with Granite before any generation or training.
Check each training sequence and each prompt plus reply budget against the limit.
Stop if any selected input does not fit. Do not replace it.
Make a new training data record and plans with Granite token totals and all five RECONCILE-FIELDS.
Check each artifact against its manifest before use.
Nemotron token counts, token hashes and `train.jsonl` hashes are not Granite pins.
Source pair identities and prompt bytes must still agree across the two series.
Publish the new training data, plan, configuration and input hashes before R0.

**GRANITE-MEMORY.** Before R0 or R1, do one LoRA step at 49,152 tokens on the pinned Granite model.
Use the complete target set, bf16, Unsloth checkpoint policy and training placement on both GPUs.
Check finite loss and gradients.
Record seconds for each optimizer step and peak memory on each GPU.
Post the memory and time record on issue 21 before R0 for the operator to set the procedure schedule.
Check the reference tree after the memory check. Keep its adapter in a different record from R1.
Do not use its weights or optimizer values for R1.
Do not use Nemotron's memory record as this check.
The shorter-context alternatives in RUN-MEMORY do not apply to Granite.
Stop on failure. Training cannot start until this exact memory check is satisfactory.
Do the memory check again before training if the model, environment, placement or training path changes.

**GRANITE-R0.** Freeze the same 100 held-out source IDs, text, code maps, kinds and bins as REWRITE-KIT.
Use Granite's new token plan for two procedures without an adapter.
Keep model, environment, template, inputs and decode values identical across both R0 procedures.
Apply REWRITE-DECODE, REWRITE-CHECK and REWRITE-SPREAD without changes to the checker or decode rule.
Use greedy decode, one beam and `min(8192, len(masked_input) // 2 + 256)` reply tokens.
Keep this approved task decode rule if the model card gives different suggested values.
Keep first outputs without repair, retry or selection.
Keep the same 30 source IDs from seed 2031 for review of the first R0 procedure.
Complete the R0 report and its blind review before Granite R1.
Publish that report through a draft PR before training.

**GRANITE-R1.** Start from the base Granite model with a fresh adapter for four epochs.
Use the exact 200-pair selection under GRANITE-DATA and the targets under GRANITE-TARGETS.
Use learning rate `1e-4`, cosine schedule, 10 warmup steps and seed 0.
Use batch size 1 and gradient accumulation 8.
Keep `gradient_checkpointing="unsloth"`, `logging_steps=1`, `device_map="balanced"` and `data.format="tokenized"`.
Use a separate configuration with these values.
All remaining trainer values follow `config/rewriter-r1-e4.toml` at `ce869065b2f7581893b97d55c8d09ba4bf08c023`.
The cosine schedule spans the complete four-epoch procedure from its start.
With 200 pairs, record 25 optimizer steps per epoch and 100 steps in total.
Do not put pairs together in one sequence or continue another adapter.
Apply TRAIN-TOKENIZED, TRAIN-MASK-REACHES-LOSS, TRAIN-EPOCH-ADAPTERS, TRAIN-RECORD and TRAIN-STOP.
Save one adapter at the end of each epoch and check it against its manifest.

**GRANITE-SCORES.** Score all four epoch adapters on the same frozen Granite held-out plan.
Apply the same decode, checker, placeholder and literal checks as GRANITE-R0.
Report accepted results out of 100, by kind and by the three source-length bins.
Report failures by rule, placeholders, literals, missing and empty outputs, and token-limit outputs.
Compare each epoch with the first Granite R0 procedure under unchanged RUN-MARGIN.
Use the absolute rate difference of Granite's two R0 procedures as its decode margin.
Keep Nemotron scores in a separate comparison table with the model and procedure identities visible.
Do not use Nemotron's R0 or two-epoch R1 as Granite's reference.
This series has no earlier two-epoch Granite R1 procedure.
Do not pool the samples or use a cross-model score difference to change RUN-MARGIN.

Before training, freeze the 50-pair sample from the same pair sequence with seed 2034.
Apply REWRITE-R1-E4-TRAIN-SCORES, with a new Granite token plan and denominator 50.
The selected pair identities and sequence must equal the Nemotron R1-E4 training sample.
Give each source request no target text. Score every epoch once on this plan.
Report each training rate beside its held-out rate and their difference in percentage points.
Keep this training diagnostic in a different record from the held-out evaluation.

**GRANITE-REVIEW.** Apply REWRITE-REVIEW with the same 30 source IDs from seed 2031 and the pinned review prompt.
Use the first R0 procedure and each saved epoch's first outputs.
Prepare a new R0 batch and a new four-epoch batch, with opaque IDs and the seed-2032 shuffle.
Keep model, procedure and epoch identities outside the grader's input and its directory.
Do not use Nemotron verdicts or put them in the new grader's context.
Check placeholders and literals before code spans are put back.
Keep unavailable items without replacements and give no invented verdict.
Report the four verdict counts and reviewed denominator for each procedure and epoch.
Calculate defect rates only on reviewed outputs that the STE checker accepted.
The executor prepares the batches and uses the `ingest` command with all three pinned hashes.
The review seat manages the separate `claude-opus-5-5` session under REWRITE-R1-E4-REVIEW.
The operator opens each grader session. The review seat gives its instruction and any format correction.
Keep the transcript, UTC times, model ID, invocation, messages, corrections and final verdict hash.
No review text or verdict enters training data.

**GRANITE-RECORD.** Use one clean merged commit for the memory check, training data, plans, R0, R1 and scores.
The producer commit must equal the launch commit in every new record.
Check that repository modules come from that checkout before and after every procedure.
Keep their file hashes and `weaver_train.__file__` in the local record.
Check the training producer before the first score and after every score.
Stop on an absent, dirty or different producer or import identity.
Keep the model, environment, template, input, checker, adapter and configuration identities with each result.
Keep complete per-step loss and recorded learning rate.
Put these records together by step identity.
For each epoch, report the arithmetic mean of its logged optimizer-step losses and the quantity of those steps.
Report wall time for every model procedure.
Record peak memory for each GPU with `max_memory_allocated` and `max_memory_reserved`.
Use new deposits under REWRITE-RECORD, with verified manifests, README and SHA256SUMS.
Post each start, result or stop on issue 21 and keep its status pane current.
Keep source, target, rewrite and grader text outside git.
Report the numbers and limits without a cause label or recommendation.
Stop after the four-epoch report. R2 and loop use are outside this approval.

**GRANITE-TESTS.** Check the exact template output and token-label boundary with the pinned tokenizer before a model procedure.
Check `pad_token_id` values before and after loading and check unchanged `eos_token_id` values.
Check failure records. Reject changed or absent IDs.
Check the real Unsloth change with a small model and check labels from `assistant_masks` for one intact sequence.
Use a small dense Granite model for native load, Unsloth, bf16 and split-GPU tests.
Check all seven targets in every layer and reject missing coverage or extra trainable parameters.
Check four saved adapters, finite losses and complete step and learning-rate records.
Reject wrong model, tokenizer, template, source, sample, producer or adapter identities.
Test that Granite and Nemotron artifacts cannot be used as each other's references.
Check both R0 procedures, unchanged RUN-MARGIN, the 100-source and 50-pair denominators, and unavailable review items.
Check review preparation and the `ingest` command for the Granite baseline and all four distinct epochs.
For each new rejection check, remove that check for a mutation test.
With that check absent, the test must not be satisfactory.
Each rejection test checks the specific reason, with other checks satisfactory.
Keep the full Nemotron test suite, with GPU tests, satisfactory.

[granite-series]: https://github.com/toddwbucy/WeaverTrain/issues/21#issuecomment-6669853774
[granite-targets]: https://github.com/toddwbucy/WeaverTrain/issues/21#issuecomment-6689076884
[granite-store]: https://github.com/toddwbucy/WeaverTrain/issues/21#issuecomment-6697186384
[granite-padding]: https://github.com/toddwbucy/WeaverTrain/issues/21#issuecomment-6699693082
[granite-card]: https://huggingface.co/ibm-granite/granite-4.2-30b/blob/9e668ce1c538387ef24d3644e9b0606647762636/README.md
[granite-disclosure]: https://github.com/ibm-granite/granite-4.2-language-models/blob/75355e6ef8c17d72a7b3736654ab525352f5e0b8/disclosures/granite_4.2_30b_disclosure.json

[trial]: https://github.com/toddwbucy/WeaverTrain/issues/36
[trial-ruling]: https://github.com/toddwbucy/WeaverTrain/issues/36#issuecomment-6664629876
[trial-correction]: https://github.com/toddwbucy/WeaverTrain/pull/38

[ruling]: https://github.com/toddwbucy/WeaverTrain/issues/17#issuecomment-6605286044
[answers]: https://github.com/toddwbucy/WeaverTrain/issues/17#issuecomment-6608618052
[folded]: https://github.com/toddwbucy/WeaverTrain/issues/17#issuecomment-6610000291
[audit]: https://github.com/toddwbucy/WeaverTrain/issues/17#issuecomment-6607188590
[cost]: https://github.com/toddwbucy/WeaverTrain/issues/17#issuecomment-6611515962
[cost-review]: https://github.com/toddwbucy/WeaverTrain/issues/17#issuecomment-6611554176
[approvals]: https://github.com/toddwbucy/WeaverTrain/issues/17#issuecomment-6614461171
[prompt-review]: https://github.com/toddwbucy/WeaverAgent/pull/448#issuecomment-6614454936
[workflow]: https://github.com/toddwbucy/WeaverAgent/pull/448
[seed-record]: https://github.com/toddwbucy/WeaverAgent/issues/449
