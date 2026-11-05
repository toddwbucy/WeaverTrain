# weaver-train Spec: LoRA for STE100 text conversion

**Status:** Approved in PR 26. The procedure sizes keep their approval.

**Version:** 0.2 **Date:** 2026-11-04 **Parent:** `docs/decisions.md`

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
Tests and procedure configuration go in new files in `tests/` and `config/`.
Use the current trainer without changes to its code.
Each new piece of code cites the clause that gives it authority.

The reference freeze for issue 15 continues until that procedure is complete.
These files stay unchanged: `ste_convert.py`, `ste_check.py`, `ste_score.py`,
`ste_prompt.py` and `ste_kit_run.py` in `scripts/`.
The `ste/` tree, the 100-segment kit and `prompt-e21370fcd140.json` also stay unchanged.
The emitter code and Spec stay unchanged.
This document and the decision entry are the first change.
The pair code follows the merge of this document.

**REWRITE-LIMITS.** Use the pinned Nemotron 30B A3B model in bf16 on the two A6000s.
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
Use batch size 1, gradient accumulation 8, two epochs and seed 0.
Keep Unsloth `gradient_checkpointing="unsloth"` and the verified placement on the two
GPUs.
Do not put pairs together in one sequence.
Start each training procedure from the base model with a new adapter.
Save and score the adapter from each epoch.

## 5. Procedure sequence and cost

**REWRITE-ORDER.** Open a different run-log issue for the rewriter series.
Record the merged Spec, code, frozen inputs and procedure values before R0.
The operator gave approval for the sizes in section 10 on 2026-11-03.
Changed sizes must have approval before their dependent procedure.
The decision on repair data follows the complete R0 report, as section 10 tells.
Do not use the emitter's R0 or R1 as a rewriter baseline.

1. R0 uses the untrained 30B model on the frozen 100-segment rewrite kit, twice.
2. R1 uses 200 pairs for two epochs, then stops.
3. R2 uses 3,383 pairs for two epochs, only on the operator's instruction.

Complete R0 and its review of the sense before R1.
The operator then gives a decision on the open cell for repair data.
Report R1 wall time, maximum memory on each GPU, the complete loss curve and both epoch
scores.
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
- **REWRITE-REVIEW:** Check the prompt hash, blind sequence, code spans put back,
  missing cases, defect denominators and override records.
- **REWRITE-RECORD:** A procedure with a failure keeps its records and cannot overwrite
  a completed directory.
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
R1 uses 200 pairs and two epochs. R2 uses 3,383 pairs and two epochs.
Both use seed 0 and the costs in section 5.
The size approval does not start R2.
The operator must give the instruction for R2 after the R1 report.

**Repair training in R1.** Open until the new R0 report.
The operator gives a decision about whether R1 also trains on repair conversations.
The decision can move that work to R2.
This Spec keeps those conversations outside training.
A decision to use them must have a Spec change for masks, counts and costs before
implementation.

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
