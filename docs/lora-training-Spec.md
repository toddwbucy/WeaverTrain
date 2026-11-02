# weaver-train Spec: LoRA training on STE100 trajectories

**Status:** DRAFT. Awaiting the operator's rulings named in section 10.

**Version:** 0.3 **Date filed:** 2026-11-02 **Parent:** `docs/decisions.md`

**Editorial:** ASCII only, no em-dashes, no semicolons, none of the Working Rules'
banned words, absolute dates. Clause names are bold and
stable. A merged version of this Spec authorizes the code it names, and where code and
this Spec disagree, the same change fixes whichever is wrong.

## 0. Scope and owning rulings

This Spec covers the code that turns converted trajectories into a LoRA adapter on
`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16` and scores the adapter against the
untrained model. It names four pieces: a build step that renders and labels the
training text (section 2), assistant-only loss (section 3), the trainer changes
(section 4) and a held-out emission scorer (section 6). Section 5 fixes the run plan,
and section 8 names the reconciliation step with the serving loop.

It inherits these rulings from `docs/decisions.md` and restates none of them:
Nemotron only, bf16 only, LoRA on attention `q/k/v/o_proj` plus Mamba `in_proj` with
no expert adapters, the `python-hf` engine label, read-only reference weights,
`pin_layer_devices` on every split-model path, and stop and report on any failure.

The input is the converted data of the STE batches (`reports/2026-11-01-ste-batch-200.md`
and `reports/2026-11-01-ste-batch-200-2.md`): 387 trajectories in two files, each with
a run record that holds its sha256. A third batch may be added. The code reads any
number of converted files and does not assume a count.

One carve-out from the Nemotron-only scope: a render-only build (BUILD-RENDER-ONLY)
may name another verified model tree, so that RECONCILE-EARLY can run against a
smaller model. A render-only build never loads weights and never feeds training.

Out of scope: the SWE benchmark (section 7), any rewriter adapter, any change to the
STE conversion scripts, and expert adapters.

## 1. What the adapter is for

**PURPOSE-EMITTER.** The adapter is an emitter. Run as an OpenHands-style agent on a
software task, the model writes its own narration and its final report in STE100 while
its tool calls stay as useful as before. The training data is exactly that behavior:
whole trajectories in which only the agent's prose was converted. A rewriter adapter,
which converts text it is handed, is a different task with different data and is not
built here. (Proposed. Ruling owed, section 10.)

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
`enable_thinking` is a ruling owed, section 10.)

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

**BUILD-RENDER-ONLY.** With `--render-only --model NAME`, the command writes a
`build.json` and the RECONCILE-RENDER renderings, and no `train.jsonl`. NAME may be any
model entry in `config/models.toml` whose tree verifies against its manifest. A
manifest that covers only the tokenizer files is enough, since a render-only build
reads the tokenizer and template and never loads weights. The trainer refuses a
render-only build. Adding a model entry, such as the 4B's tokenizer files at the
revision its GGUF was converted from, is a download and waits on the operator's go
like any other.

## 3. Loss on the assistant turns only

**LOSS-ASSISTANT.** The loss is computed only on tokens whose mask is 1. System,
issue, tool output and template tokens add nothing to the loss. In batch 1, tool
output was 57% of all tokens and STE prose 5%. Without this clause most of training
goes to text the model does not write.

**LOSS-UNWEIGHTED.** Every masked token weighs the same. STE prose is about 16% of the
assistant tokens, and the rest is tool calls the model already writes. Weighting prose
above tool calls is not built here. It is an open cell (section 10), to be tried only if
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
choice. Either way the test in section 9 must pass, because a mask that is silently
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
and the curve is what tests them. (Ruling owed on changing any of them, section 10.)

**RUN-ORDER.** Runs happen in this order, and each waits for the one before:

1. **R0, baseline.** The scorer on the untrained model.
2. **R1, 100 rows.** A timing and sanity run. It stops after this run and reports wall
   time, peak memory, the loss curve and its score, before any longer run.
3. **R2, 200 rows**, then **R3, all rows**, each on the nested subsets of BUILD-ORDER.

Each run's epoch adapters are scored. The curve is the held-out pass rate and the
validity rate against rows trained on, at epochs 1 and 2.

**RUN-MARGIN.** This margin is fixed here, before R0 runs, and is not changed after any
score is seen. Every comparison in the curve is between two scorings of the same
segments, so it is paired. For two scorings A and B, b counts the segments that pass
under B and fail under A, and c counts the reverse. B is higher than A only when both
hold: an exact two-sided sign test on b and c gives p below 0.05, and the difference
in pass rate is larger than SCORE-SPREAD's decode spread. If either fails, the two are
equal for this Spec's purposes ("flat"). The same rule, applied to valid tool calls,
decides a fall in validity. On 100 segments, differences under about 10 points will
usually read as flat. That is a limit of the held-out set, recorded with each reading,
not a reason to loosen the margin. The two noises are separate: SCORE-SPREAD is decode
nondeterminism between identical runs, and the sign test covers sampling error on the
segments.

**RUN-READ.** If R3 is higher than R2 under RUN-MARGIN, more data is worth getting. If
they are flat, the next change is LOSS-UNWEIGHTED's open cell, not more data. If an
adapter's validity is lower than R0's under RUN-MARGIN, the adapter is reported as
costing skill.

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
selection are read against their prompt: does the prose fit what the agent is doing,
and is the next action sensible. Verdicts are fits, odd or wrong. The checker cannot
see this, as the conversion batches showed. The reader is a Claude model session,
`claude-opus-5-5`, given the review prompt `ste/review-prompt.md`. That file is
committed with this code and holds no standard text, and its sha256 and the model id
go into the score record, as SCORE-CHECK records the checker's prompt hash. The read is
blind: the segments of every run being compared are shuffled together, and the reader
is not told which model wrote which. Neither Gemini nor DeepSeek reads, because both
wrote training or reference data. The operator may read any segment, and an operator
verdict replaces the model's for that segment and is marked as such.

**SCORE-SPREAD.** R0 runs twice. Greedy decoding on a split model can still differ
between runs, and the difference between the two R0 scores is the decode spread
RUN-MARGIN uses.

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

## 8. Reconciliation with the serving loop

The STE conversion loop is built and verified on a second machine, through the SPU, as
WeaverAgent's bootstrap agent (`toddwbucy/WeaverAgent` #430 and #431), while this
machine trains the adapter. The two halves will not finish in step, and training-side
work goes on while the loop is built. Reconciliation is therefore a named step, not a
surprise. The interface between the two sides is the build record of section 2 and
nothing else: no code, fixture or path couples this repository to WeaverAgent.

**RECONCILE-STEP.** Reconciliation sits between two milestones: the loop verified end
to end on the second machine, and the loop running with this Spec's adapter on this
machine. It holds the three checks below. The loop does not run with the adapter until
all three are recorded under RECONCILE-RECORD.

**RECONCILE-FIELDS.** The SPU side must match these `build.json` fields of the build
the adapter was trained on: the model `tree_hash` (the tokenizer comes from it), the
template hash, the `tools` choice, the `enable_thinking` value and `max_seq_length`.
BUILD-RENDER and BUILD-OUTPUT already record them. This clause adds no field, and a
change to any of them after reconciliation reopens it.

**RECONCILE-RENDER.** Training stays in Python, and the SPU does not train, but it must
feed the model the tokens the trainer saw. Two prompts are each rendered two ways,
through BUILD-RENDER with the build record's values and through the SPU's family entry
for the model, and each pair of token ID sequences is compared:

1. **The trajectory turn.** One held-out prompt as SCORE-PREFIX builds it, with the
   row's `tools` passed as the build record says.
2. **The loop turn.** The turn the loop's stage 5 sends (ruling P of the #431 brief):
   the rules prompt `e21370fcd140` as the system message and one kit segment, masked,
   as the user message, with `tools` absent. This takes a different path through the
   chat template, and the loop sends it however PURPOSE-EMITTER is ruled.

Each prompt's message list is written once, as JSON, and both sides render that same
list, so the comparison tests the rendering and not two ways of building the messages.
The loop turn's list holds standard text, so it lives only in the RECONCILE-RECORD
deposit, never in git. If a pair matches, the adapter sees in serving what it saw in
training for that turn. If it differs, the SPU's rendering is fixed to match the build
record, never the reverse. The GGUF carries the chat template in its metadata, so a
match is expected, but it is measured, not assumed.

**RECONCILE-ADAPTER.** The bf16 PEFT adapter is converted to a GGUF LoRA the SPU loads.
The SPU has no LoRA load path yet, and that work is WeaverAgent's (issue 4 of the #431
brief). This Spec's obligation ends at an adapter that passes TRAIN-EPOCH-ADAPTERS and
a recorded sha256 for each adapter handed over.

**RECONCILE-DRIFT.** Anything this side changed while the loop was being built is read
against what the loop assumes: the template, `enable_thinking`, the `tools` choice and
`max_seq_length`. Each difference is resolved by RECONCILE-RENDER's rule: the build
record is the reference, unless a ruling changes the build and a new adapter is
trained under it.

**RECONCILE-EARLY.** The second machine can run RECONCILE-RENDER now on the
Nemotron-3-Nano-4B GGUF, against a render-only build (BUILD-RENDER-ONLY) made for that
model. Two things are needed for that. The build names the 4B's model entry, and the
second machine holds the 4B's Hugging Face tokenizer files beside its GGUF, at the
revision the GGUF was converted from, since the GGUF alone cannot produce a
BUILD-RENDER rendering. That proves the method before this machine is free. Only this
machine can confirm it for the 30B A3B.

**RECONCILE-RECORD.** Each reconciliation is one deposit under `weaver-testing/` on the
shared bulk store, named `lora-reconcile-<date>-<model>-<first 8 of the adapter
sha256>` (`none` for the early check), with a `SHA256SUMS` and a README, as RUN-OUTPUT
deposits are. It holds the `build.json` the adapter was trained on (or the render-only
build), both message lists, both sides' token ID sequences for each prompt and the
result of each comparison, the PEFT adapter's sha256 and the GGUF LoRA's sha256, and
the RECONCILE-DRIFT table. Both repositories cite the deposit by name, so neither seat
takes the other's word.

## 9. Enforcement

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
- **RECONCILE-FIELDS:** `build.json` carries every field the clause names, and the
  scorer refuses a build missing one. The render check itself is a recorded act on
  real artifacts, not a unit test.
- **BUILD-RENDER-ONLY:** a render-only build on the stand-in model's tokenizer writes
  `build.json` and both renderings and no `train.jsonl`, and the trainer refuses it.
- **RUN-MARGIN:** the sign test and the spread rule, on fixed pass and fail vectors,
  give the expected "higher" and "flat" readings, including a case where the sign test
  passes but the difference is inside the spread.
- **SCORE-REVIEW:** the score record carries the review prompt's sha256 and the model
  id, and the shuffled selection holds no label of which run wrote a segment.
- `tests/test_independence.py` still passes. The two new commands are scripts and may
  import scripts, and no module under `src/` imports another except `manifest`.

## 10. Open cells and rulings owed

**Emitter or rewriter (PURPOSE-EMITTER).** Proposed: emitter. Settled by the
operator's ruling. WeaverAgent's ruling J currently reads the other way, toward a
rewriter, so the two documents disagree until this cell is ruled. The reconciliation
note of 2026-11-02 does not settle it.

**The reconciliation render check (RECONCILE-RENDER).** Settled by the two token ID
comparisons, the trajectory turn and the loop turn, first on the 4B GGUF on the second
machine (RECONCILE-EARLY), then on the 30B A3B here.

**The 4B's tokenizer files (RECONCILE-EARLY).** Settled by the operator's go to add the
4B's model entry at the revision its GGUF was converted from.

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
