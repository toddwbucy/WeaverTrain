# weaver-train Spec: small 4B LoRA fixture

ASD-STE100:9 thesaurus: 022df3d809593696766893bf95f915229628497b89ef571dbc997ac8585224da

**Status:** MERGED. These clauses take effect when the operator merges this amendment.
**Version:** 0.1 **Date:** 2026-11-04
**Document ID:** `lora-4b-fixture-Spec-amendment`
**Parent:** `docs/decisions.md`
**Lexicon:** `ste/` at `ab63a918b569e5b8673dc2e78e66575ee9df1bd7`
**Approved words SHA256:**
`c622bc230bc0c29512ef16990f527bf32a5ffaffbe955f73751251a7e5ab4693`

## 0. Authority and purpose

**FIXTURE-0.** This amendment is for [WeaverAgent issue 473][epic], item 2.
On 2026-11-04, the operator chose a small 4B adapter from stored STE data.
The [executor receipt][direction] records this direction and its limits.
The output must change sufficiently to test the infrastructure.
STE quality is not an acceptance condition.
The executor proposes the numeric limits below for this purpose.

The source baseline is `0a44847611a5fe157fd3dd2e2035d98c7ab7d28e`.
The [training Spec][training] gives approval for 30B training and 4B token preparation.
The [rewriter Spec][rewriter] also limits training to 30B.
This amendment adds one different 4B fixture procedure.
It changes no value or result in either 30B series.

| Clause | Current rule | Fixture exception |
| --- | --- | --- |
| FIXTURE-1 | Training scope and BUILD-RENDER-ONLY | Train one approved 4B base |
| FIXTURE-2 | PAIR-BALANCE | Select 16 short stored pairs |
| FIXTURE-3 | RENDER-LAYOUT and RENDER-LENGTH | Short fixture prompt and 2,048 tokens |
| FIXTURE-4 | RUN-CONFIG and REWRITE-CONFIG | Rank 8 and 32 optimizer steps |
| FIXTURE-5 | REWRITE-MEASURE and REWRITE-ORDER | Mechanical result with no STE score |
| FIXTURE-6 | RUN-OUTPUT and REWRITE-RECONCILE | Fixture deposit and conversion record |

Training uses Python on the training machine. Application uses the Rust SPU machine.
The 30B family entry and R1 emitter test stay in their 30B lane.
WeaverAgent PR 450 and the R0 composition stay on hold.
This fixture gives no authority to seed or deploy an agent.
It does not complete item 2 before the SPU records are complete.

**FIXTURE-0.1.** Merge gives authority for fixture code that cites these clauses.
Use a separate fixture command and configuration.
Use the current trainer without a new training engine.
Keep the current series commands, configurations and frozen checker files unchanged.
Import no code from another repository.
Large artifacts and machine paths stay outside git.
Do not start training from this document PR.

## 1. Base identity and availability

**FIXTURE-1.** Use `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` at this source revision:

`3eab5a7bdfb5e669104f0014242972da1db50771`

The [producer tree][model] has the BF16 weights and the Q4_K_M GGUF.
This source location alone does not prove that local files agree.
Before training, check the complete BF16 tree against its manifest.
Record the tokenizer and template hashes from that same revision.
Before conversion, check the exact source relationship to the SPU base.
Record the runtime GGUF SHA256 and the SPU's canonical BLAKE3 identity separately.
Stop if the training base and runtime base have different ancestry.

The merged model registry has only the 30B entry at the source baseline.
The complete local 4B BF16 tree has no verification record for this procedure.
Record the training machine's inventory and manifest verification before any model load.
If the weights are absent, get the operator's download approval first.
Before any model load, add the pinned model entry and manifest through review.
This requirement applies to local weights and new downloads.
The previous tokenizer approval gives no approval to download weights.
Keep reference weights read-only and check them again after training.

## 2. A fixed small data set

**FIXTURE-2.** Use the accepted pair pool from batches `200` and `200-2`.
Apply PAIR-INPUTS, PAIR-HELDOUT, PAIR-TARGET and PAIR-PROVENANCE from the rewriter Spec.
Use their current hashes in `config/rewriter-inputs.toml`.
Keep the stored source, target, placeholders and provenance unchanged.
Remove every trajectory UUID in either frozen evaluation kit.
Do not use a repair conversation, new rewrite or new dataset download.

Keep pairs whose input and target each have at most 200 Unicode characters.
Count the stored text with placeholders, before template application.
Hash the UTF-8 bytes of `0\n<batch>\n<segment_id>` with SHA256, without a final LF.
Sort all eligible pairs from both batches in one sequence by that hash.
For equal hashes, compare batch, then segment ID, as strings.
Put the smallest value first.
Select the first 16 eligible pairs in that sequence, without replacement.
The fixture has no length-bin quota or narration quota.
Record the ordered IDs, their hash and the pair-pool manifest.
Stop if fewer than 16 pairs qualify.
Stop if a selected sequence is longer than the token limit.
Do not truncate text or select another pair after that failure.

These 16 inputs are also the mechanical test set.
The same inputs can test application because generalization is not the measurement.
No result from this set is an evaluation of STE quality.

## 3. Fixture prompt and token labels

**FIXTURE-3.** Use this exact system text, without a final LF:

```text
Rewrite the text in STE100. Keep its sense and placeholders. Give only the rewrite.
```

The user message is `"<<<TEXT\n" + masked_input + "\nTEXT>>>"`.
The assistant target is the stored text with placeholders.
Use the pinned model template with `enable_thinking=false` and no tools argument.
The complete sequence has one system, one user and one assistant message.
The prompt is specific to this fixture. The full production prompt keeps its authority.

Record the prompt bytes, prompt SHA256, template SHA256 and complete message lists.
Store the full token IDs and binary `assistant_masks` values for each pair.
The complete token sequence must start with the exact generation prefix.
Use 0 for prefix tokens and 1 for the final assistant suffix.
Keep the terminal token in that suffix, as in RENDER-MASK.
Each sequence must have target tokens and at most 2,048 total tokens.
Keep one sequence per row. Do not truncate tokens.
Record the full model tree hash and this limit in `build.json`.
Write `train.jsonl` and seal the data record with a tree manifest.
Use a clean commit and keep all input hashes in the data record.
The trainer must reject an incomplete, changed or render-only data record.

## 4. One bounded training procedure

**FIXTURE-4.** Use these values in the separate fixture configuration:

```toml
[lora]
r = 8
alpha = 16
dropout = 0.0
target_modules = ["q_proj", "k_proj", "v_proj", "o_proj", "in_proj"]
bias = "none"

[train]
max_seq_length = 2048
learning_rate = 1e-4
max_steps = 32
per_device_batch_size = 1
gradient_accumulation_steps = 1
warmup_steps = 0
lr_scheduler = "constant"
seed = 0
gradient_checkpointing = "unsloth"
logging_steps = 1
device_map = "balanced"

[data]
format = "tokenized"
```

The cap counts optimizer steps. Do not also set `num_train_epochs`.
Use BF16 reference weights and the pinned environment on the two A6000s.
Apply `pin_layer_devices` when the model spans both devices.
Before the procedure, use `scripts/setup-env.sh --only verify --reverify`.
Record its commit, UTC time and result.
Stop on an environment, kernel, memory, data or training failure.
Keep the partial record and do not retry with changed values.

All five specified target classes must have adapters. Record their adapted modules.
Keep the trainer's target-coverage refusal and component-coverage record.
Do not remove `in_proj` to get past a conversion or backend failure.
Record each step's loss, elapsed time and peak device memory.
Loss is diagnostic data, not a quality threshold.
Save only the final adapter for acceptance. Do not select a favorable checkpoint.
No parameter search, automatic extension or second training procedure is approved here.

## 5. Mechanical acceptance

**FIXTURE-5.** First compare Python base-only and adapter outputs on all 16 inputs.
Use the exact saved prefix tokens, greedy decode and a 128-token output cap.
Record terminal conditions, token IDs and output bytes for each input.
Use scale 1 for the adapted procedure.
Use a new model residency for each procedure.
Use no random choice, repair or language-quality filter.
An output change must occur in at least one output token sequence.
If none changes, report an inconclusive fixture and stop.
Do not change the prompt, scale, pair set or step count to force a pass.

Make the GGUF adapter under [WeaverAgent issue 435][conversion].
Account for every target. This includes `in_proj` to `ssm_in`.
Keep the PEFT rank and alpha semantics. Runtime scale is a separate value.
Stop on a missing, renamed incorrectly or ignored target.
Do not claim conversion success from a file's presence alone.

Use [LORA-1 through LORA-7][spu-spec] for the Rust SPU fixture test.
Compare new base-only, scale-0 and scale-1 residencies on all 16 inputs.
Do each case twice from a fresh residency with identical tokens and decode settings.
Use one fixture process per residency, for six SPU processes in total.
At the pinned backend, scale 0 keeps the adapter loaded and unattached.
This control measures load cost and residency without adapter application.
The two procedures for each case must have equal output token sequences.
The scale-0 outputs must equal base-only outputs.
At least one scale-1 output must be different from its base-only output.
Keep all 16 results, with unchanged outputs and failures.
This comparison proves application only when the admission and attachment records agree.

Prove scale-1 attachment before prefix decode.
Capture every target's device placement.
Prove cleanup on success and on the required failure paths.
At this pin, `llama_set_adapters_lora` always gives 0.
Use fault injection for the attachment-failure check.
The real procedure records attachment success and the target placement observations.
Keep the resource observations and refusal checks from LORA-6 and LORA-7.
A successful loader handle or a fake backend cannot replace these records.
Record Python/SPU differences separately. The operator's accepted numerical difference
stays open.
Neither text similarity nor a successful STE check can close that cell.

## 6. Deposit and stop

**FIXTURE-6.** Make a fresh fixture deposit outside git and the reference tree.
Record the code commit, environment identity, configuration and data manifest.
Record the source model revision, complete model manifest and all template identities.
Keep the final producer record and adapter files unchanged after training.
Hash the final producer record and adapter tree before conversion or comparison.
This capture does not claim a trainer-written seal across the earlier interval.
The deferred producer-seal work in [PR 27][producer-seal] stays in a different task.

Record the converter commit, exact arguments and source adapter tree hash.
Use `--outtype f32` for this fixture's GGUF adapter conversion.
The SPU reader refuses quantized adapter tensors, such as `q8_0`.
Record the converted adapter SHA256 and the target-name table.
Keep the runtime base identities, sampler settings and complete comparison records.
Label the result `fixture-only`, with no STE quality claim.
Keep failure and inconclusive records without a success label.
After the report, freeze the fixture and stop.
Get an operator decision from the failed measurement before more training.

## 7. Open cells and execution sequence

**FIXTURE-7.** These cells stay open until their named records are available.

| Cell | Record that closes it |
| --- | --- |
| BF16 availability | Training machine inventory and model manifest verification |
| Download approval, if absent | Operator approval for the exact source revision |
| Selected pair identities | Pool verification, 16-pair selection and ordered-ID hash |
| Memory and duration | Peak memory and elapsed time per step |
| Mamba application | Complete target-name table and SPU device observations |
| Observable application | Two base-only, scale-0 and scale-1 token records |
| Python/SPU limit | Measured differences and an explicit operator decision |

Review and merge this amendment before fixture code.
Review the fixture code and the completed base inventory before the request for the
procedure.
Get download approval before acquisition of an absent weight file.
The operator then authorizes the bounded training and mechanical comparison.
The numeric proposal alone is not a completed training or SPU result.
Items 3 and 4 of issue 473 wait for that issue's prerequisites.

[epic]: https://github.com/toddwbucy/WeaverAgent/issues/473
[direction]: https://github.com/toddwbucy/WeaverAgent/issues/473#issuecomment-6642480452
[training]: lora-training-Spec.md
[rewriter]: lora-rewriter-Spec.md
[model]: https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16/tree/3eab5a7bdfb5e669104f0014242972da1db50771
[conversion]: https://github.com/toddwbucy/WeaverAgent/issues/435
[spu-spec]: https://github.com/toddwbucy/WeaverAgent/blob/4493be6b7b79512a375eb0d9fdf28e02de58aec7/docs/crates/weaver-spu/weaver-spu-lora-loading-Spec-amendment.md
[producer-seal]: https://github.com/toddwbucy/WeaverTrain/pull/27#issuecomment-6640982784
