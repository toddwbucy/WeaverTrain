# weaver-train Spec: LoRA training on STE100 trajectories

**Status:** DRAFT. Awaiting the operator's rulings named in section 9.

**Version:** 0.1 **Date filed:** 2026-11-02 **Parent:** `docs/decisions.md`

**Editorial:** ASCII only, no em-dashes, absolute dates. Clause names are bold and
stable. A merged version of this Spec authorizes the code it names, and where code and
this Spec disagree, the same change fixes whichever is wrong.

## 0. Scope and owning rulings

This Spec covers the code that turns converted trajectories into a LoRA adapter on
`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16` and scores the adapter against the
untrained model. It names four pieces: a build step that renders and labels the
training text (section 2), assistant-only loss (section 3), the trainer changes
(section 4) and a held-out emission scorer (section 6). Section 5 fixes the run plan.

It inherits these rulings from `docs/decisions.md` and restates none of them:
Nemotron only, bf16 only, LoRA on attention `q/k/v/o_proj` plus Mamba `in_proj` with
no expert adapters, the `python-hf` engine label, read-only reference weights,
`pin_layer_devices` on every split-model path, and stop and report on any failure.

The input is the converted data of the STE batches (`reports/2026-11-01-ste-batch-200.md`
and `reports/2026-11-01-ste-batch-200-2.md`): 387 trajectories in two files, each with
a run record that holds its sha256. A third batch may be added. The code reads any
number of converted files and does not assume a count.

Out of scope: the SWE benchmark (section 7), any rewriter adapter, any change to the
STE conversion scripts, and expert adapters.

## 1. What the adapter is for

**PURPOSE-EMITTER.** The adapter is an emitter. Run as an OpenHands-style agent on a
software task, the model writes its own narration and its final report in STE100 while
its tool calls stay as useful as before. The training data is exactly that behavior:
whole trajectories in which only the agent's prose was converted. A rewriter adapter,
which converts text it is handed, is a different task with different data and is not
built here. (Proposed. Ruling owed, section 9.)

**PURPOSE-MEASURE.** Success is measured, never asserted. The adapter has worked when
the held-out STE pass rate (section 6) rises above the untrained model's while the
tool-call validity rate does not fall. A run that raises the pass rate and lowers
validity is reported as a trade, not a success.

## 2. Building the training input

A new command, `scripts/lora_build.py build`, writes the trainer's input. It never
loads model weights. It reads only the tokenizer files of the verified model tree.

**BUILD-INPUTS.** The command takes one or more converted files. For each, it reads
the run record beside it and refuses the file if the file's sha256 differs from the
record's `converted_sha256`. It refuses a row that two inputs both hold (same dataset
row number).

**BUILD-HELDOUT.** The command takes the held-out kit (section 6) and refuses to run if
any input row's uuid appears in it. The check is by uuid because the dataset repeats
uuids across rows, and a task in the held-out set must not appear in training under
any row.

**BUILD-RENDER.** Each row is rendered with the model's own chat template through
`apply_chat_template`, with the row's `tools` list passed as `tools` and with
`enable_thinking` set from a required argument. The messages pass through the same
field selection and argument parsing as `ste_batch.TokenCounter`. The template hash,
the `tools` choice and the `enable_thinking` value go into the build record, and the
scorer refuses a build whose values differ from its own. (The value for
`enable_thinking` is a ruling owed, section 9.)

**BUILD-LENGTH.** A row longer than `max_seq_length` tokens after rendering is dropped
whole and listed in the build record with its length. No row is truncated. Rows were
sampled at 32,768 and 32,000 tokens without `tools`, so passing `tools` may push some
over. The record counts them.

**BUILD-MASK.** Each row gets an `assistant_masks` array the same length as its
`input_ids`: 1 on tokens the assistant wrote, 0 on everything else. The tokens the
assistant wrote are found by prefix difference. For each assistant message i, the
template is rendered for messages before i with `add_generation_prompt=True`, and for
messages up to and including i. The second rendering's extra tokens are the
assistant's: its content, its tool calls and its end-of-turn token. The generation
prompt header is the template's, not the assistant's, and stays 0.

**BUILD-PREFIX.** Prefix difference is valid only if each rendering is a token prefix
of the next. The command checks this for every assistant turn of every row. On the
first row that fails, it stops and reports the row and turn. It does not skip the row
or work around the template. Nemotron's template has no `{% generation %}` markers,
which is why the mask is computed here and not by TRL.

**BUILD-ORDER.** Rows are written in a seeded order. With `--limit N`, the first N rows
of that order are written, so the subsets for the learning curve (section 5) are
nested: the 100-row set is inside the 200-row set, which is inside the full set.

**BUILD-OUTPUT.** The command writes `train.jsonl` (one row per line: `input_ids`,
`assistant_masks`, and the batch, trajectory and dataset row it came from) and
`build.json`: the input files and their sha256, the model's `tree_hash`, the template
hash, the `tools` and `enable_thinking` values, `max_seq_length`, the seed and limit,
and counts (rows in, rows dropped and why, total tokens, masked tokens, and the
masked tokens that fall in converted STE prose). It then writes a tree manifest of the
output directory with `weaver_train.manifest`. The output goes under
`$WEAVER_TRAIN_STATE_DIR`, never into git.

## 3. Loss on the assistant turns only

**LOSS-ASSISTANT.** The loss is computed only on tokens whose mask is 1. System,
issue, tool output and template tokens add nothing to the loss. In batch 1, tool
output was 57% of all tokens and STE prose 5%. Without this clause most of training
goes to text the model does not write.

**LOSS-UNWEIGHTED.** Every masked token weighs the same. STE prose is about 16% of the
assistant tokens, and the rest is tool calls the model already writes. Weighting prose
above tool calls is not built here. It is an open cell (section 9), to be tried only if
the learning curve stalls.

## 4. Trainer changes

`src/weaver_train/trainer.py` keeps its order of work (verify, load, attach, train,
save adapter only, re-verify, record). These clauses add to it.

**TRAIN-TOKENIZED.** A new required config key `data.format` takes `"text"` (today's
behavior, the `text_field`) or `"tokenized"` (`input_ids` and `assistant_masks` from
section 2). There is no default. With `"tokenized"`, the trainer refuses a dataset
whose `build.json` it cannot find or whose manifest does not verify, and it refuses a
`max_seq_length` that differs from the build's.

**TRAIN-MASK-REACHES-LOSS.** With `"tokenized"`, the labels the loss sees are -100
exactly where the mask is 0 and equal to `input_ids` where it is 1. Whether this uses
TRL's own `assistant_masks` support or a collator of ours is an implementation
choice. Either way the test in section 8 must pass, because a mask that is silently
ignored trains on everything and logs no warning.

**TRAIN-NO-PACKING.** Packing is off. Each row is one sequence, batch size per device
is 1, and no row is split.

**TRAIN-EPOCHS.** The `[train]` table takes exactly one of `max_steps` or
`num_train_epochs`. Giving both or neither is a config error.

**TRAIN-EPOCH-ADAPTERS.** With `num_train_epochs`, the adapter is saved at the end of
each epoch under `adapter-epoch-N`, adapter files only, as `check_adapter_only` already
enforces. The final adapter is also saved as `adapter`.

**TRAIN-RECORD.** The run record adds: the `build.json` sha256 and the rows trained on,
the masked token count, steps and epochs, wall time per step and in total, peak
memory per GPU, the loss per logging step, and the final loss. These are the numbers
the run plan's stop rules read.

**TRAIN-STOP.** The run stops and records `failed` on a loss that is NaN or infinite,
an out-of-memory error, a `TargetCoverageError`, or any sign that layers were placed on
CPU. It does not retry with other settings.

## 5. Run plan

**RUN-CONFIG.** The first configuration, unchanged across the learning curve:

| Setting | Value |
|---|---|
| `lora.r` / `lora.alpha` / `lora.dropout` | 16 / 32 / 0.0 |
| `lora.target_modules` | `q_proj`, `k_proj`, `v_proj`, `o_proj`, `in_proj` |
| `train.max_seq_length` | 32768 |
| `train.learning_rate` | 1e-4, cosine schedule, 10 warmup steps |
| `train.per_device_batch_size` / `gradient_accumulation_steps` | 1 / 8 |
| `train.num_train_epochs` | 2 |
| `train.seed` | 0 |

With 387 rows, an epoch is about 48 optimizer steps. These values are a first guess,
and the curve is what tests them. (Ruling owed on changing any of them, section 9.)

**RUN-ORDER.** Runs happen in this order, and each waits for the one before:

1. **R0, baseline.** The scorer on the untrained model.
2. **R1, 100 rows.** A timing and sanity run. It stops after this run and reports wall
   time, peak memory, the loss curve and its score, before any longer run.
3. **R2, 200 rows**, then **R3, all rows**, each on the nested subsets of BUILD-ORDER.

Each run's epoch adapters are scored. The curve is the held-out pass rate and the
validity rate against rows trained on, at epochs 1 and 2.

**RUN-READ.** If the pass rate is still rising between R2 and R3, more data is worth
getting. If it is flat, the next change is LOSS-UNWEIGHTED's open cell, not more data.
If validity falls by more than the run-to-run spread of R0's scorer (section 6), the
adapter is reported as costing skill.

**RUN-OUTPUT.** Each run writes to a fresh directory outside the model tree, as the
trainer already enforces. Adapters, build outputs and score files are deposited under
`weaver-testing/` on the shared bulk store with a `SHA256SUMS` and a README. Nothing
but the result note enters git.

## 6. The held-out emission scorer

A new command, `scripts/lora_score.py`, scores a model, with or without an adapter, on
the held-out set. It loads the model through the same verified path as the loader,
applies `pin_layer_devices`, and records the engine as `python-hf`.

**SCORE-HELDOUT.** The held-out set is the 100-segment kit (90 narration, 10 final
reports), frozen since 2026-11-01. None of its uuids appear in any STE batch. Its
recorded verdicts for Gemini and DeepSeek rewrites stay as reference, and the
untrained model's score is the baseline.

**SCORE-LOCATE.** The kit records each segment's uuid, its location (message index and
field) and its original text, but not its dataset row. The scorer finds the row whose
uuid matches and whose text at that location equals the kit's. No match, or more than
one, is listed in the score record and the segment is not scored.

**SCORE-PREFIX.** For a segment at message i, the prompt is messages before i,
rendered as BUILD-RENDER renders them, with `add_generation_prompt=True`. A prompt
that leaves less than the generation budget inside `max_seq_length` is listed and not
scored.

**SCORE-GENERATE.** Generation is greedy (no sampling), with a budget of 2,048 new
tokens, and stops at the end-of-turn token. Greedy decoding keeps a score repeatable,
which the curve depends on.

**SCORE-EXTRACT.** For a narration segment, the text scored is the generated message's
content before any tool call. For a final segment, it is the `message` argument of a
generated `finish` call. A generation with no such text, where the original had it, is
counted as "no prose" and is not a pass.

**SCORE-CHECK.** The prose is masked with `ste_convert.mask` and judged with the same
checker and prompt hash as the conversion (`ste_experiment.Judge`, prompt
`e21370fcd140`). The record holds pass or fail, the rules broken, the -ing count and
the word count per segment, and the totals.

**SCORE-VALIDITY.** A cheap skill signal until section 7 exists: a generated tool call
is valid if it parses and names a tool in the row's `tools` list with arguments that
satisfy that tool's required parameters. The rate is over all segments with a
generated tool call.

**SCORE-REVIEW.** For each scored adapter and for R0, 30 segments in a fixed seeded
selection are read by reviewers against their prompt: does the prose fit what the
agent is doing, and is the next action sensible. Verdicts are fits, odd or wrong. The
checker cannot see this, as the conversion batches showed.

**SCORE-SPREAD.** R0 runs twice. Greedy decoding on a split model can still differ
between runs, and the difference between the two R0 scores is the spread RUN-READ
uses.

**SCORE-OUTPUT.** Per segment, a JSON Lines record with the prompt length, the
generated text, the extracted prose, the checker result and the validity result. In
total, `score.json` with the model `tree_hash`, the adapter's sha256 (or none), the
build's template hash and `enable_thinking`, and the totals. Results under one engine
label are never pooled with another's.

## 7. The SWE benchmark

**BENCH-DEFERRED.** The frozen benchmark snapshots need a Docker harness, and none
exists yet. This Spec authorizes no benchmark code. Until the harness exists, task
skill is read only through SCORE-VALIDITY and SCORE-REVIEW, and no claim is made that
an adapter keeps or loses SWE skill. When the harness lands, the trained-minus-untrained
comparison follows `docs/benchmarks.md`.

## 8. Enforcement

Each clause has a test under `tests/`, on the stand-in model of `tests/conftest.py`
where a model is needed.

- **BUILD-INPUTS, BUILD-HELDOUT:** a converted file whose hash differs from its run
  record is refused, and a row whose uuid is in the kit is refused.
- **BUILD-MASK, BUILD-PREFIX:** on fixture rows with tool calls, the decoded masked
  tokens equal the assistant turns as the template renders them, and no system, user
  or tool token is masked. A template that breaks prefix stability makes the build
  stop.
- **BUILD-LENGTH, BUILD-ORDER:** an over-length row is dropped and listed, and the
  `--limit` subsets are nested.
- **TRAIN-TOKENIZED, TRAIN-EPOCHS:** the config errors fire.
- **TRAIN-MASK-REACHES-LOSS:** one training step on the stand-in model, with labels
  captured at the loss, shows -100 exactly where the mask is 0. This test runs on GPU.
- **TRAIN-EPOCH-ADAPTERS:** a two-epoch run on the stand-in model leaves two epoch
  adapters and a final one, adapter files only.
- **SCORE-LOCATE, SCORE-EXTRACT, SCORE-VALIDITY:** ambiguous and missing rows are
  listed, prose and `finish` messages are extracted, and invalid tool calls are
  counted.
- `tests/test_independence.py` still passes. The two new commands are scripts and may
  import scripts, and no module under `src/` imports another except `manifest`.

## 9. Open cells and rulings owed

**Emitter or rewriter (PURPOSE-EMITTER).** Proposed: emitter. Settled by the
operator's ruling.

**Thinking on or off (BUILD-RENDER).** The template supports reasoning content and
`enable_thinking`, and the data holds no reasoning content, only the `think` tool's
calls. Proposed: off, in both build and scoring, so training does not teach empty
reasoning blocks. Settled by the operator's ruling, or by scoring R0 both ways.

**Tools in the rendering (BUILD-RENDER).** Proposed: passed, so the model sees the tool
schemas it sees when run as an agent. Settled by the BUILD-LENGTH drop count with and
without: if passing them drops many rows, this cell is ruled again.

**Starting without the benchmark (BENCH-DEFERRED).** Proposed: start. Settled by the
operator's ruling.

**The run configuration (RUN-CONFIG).** Settled by the curve: if R1's loss has not
fallen by its second epoch, the learning rate is ruled again before R2.

**Weighting prose above tool calls (LOSS-UNWEIGHTED).** Settled by RUN-READ: tried only
if the pass rate is flat between R2 and R3.

**Enough data.** Settled by RUN-READ's comparison of R2 and R3. A third STE batch, if
finished, is held back as reserve unless the curve is still rising.

**The 32K bias.** All training rows are 32K tokens or shorter, about one dataset row in
ten. Whether STE holds on longer tasks is settled only by a held-out set of longer
trajectories, which needs a longer `max_seq_length` and a memory test at 64K first.
