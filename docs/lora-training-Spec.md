# weaver-train Spec: LoRA training on STE100 trajectories

**Status:** DRAFT. The operator gave the decisions of section 10 on 2026-11-02. The
`enable_thinking` cell stays open. PR #4 (`ab63a91`, the conversion code that this
Spec refers to) merges before this Spec. The operator does the 2 merges.

**Version:** 0.3 **Date:** 2026-11-02 **Parent:** `docs/decisions.md`

**Language:** STE100, on the operator's decision of 2026-11-02. The lexicon is `ste/`
at commit `ab63a91`, with the technical nouns that this version adds to
`ste/technical-nouns.toml`. The approved words come from the local copy of the
standard, `var/ste/approved-verbs.json`, sha256 `c622bc230bc0c295...`. The checker is
`scripts/ste_check.py` at `ab63a91`. Clause names, paths, keys and text in backticks
are technical names.

**Editorial:** ASCII only. No em-dashes, no semicolons and no word from the banned list of the
`Working Rules`. Use absolute dates. Clause names are bold and do not change. A
merged version of this Spec gives approval for the code that it names. If the code and
this Spec do not agree, one change makes the incorrect one correct.

## 0. Scope

This Spec is for the code that makes a LoRA adapter on
`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16` from converted trajectories. The code
also compares the adapter with the model that has no adapter. The Spec has 4 parts of
code:

1. A data script that applies the chat template and makes the labels (section 2).
2. Loss on the assistant tokens only (section 3).
3. Changes to the trainer (section 4).
4. A score script for the held-out set (section 6).

Section 5 gives the sequence of runs. Section 8 gives the step that aligns this
training with the SPU loop.

This Spec does not write the decisions in `docs/decisions.md` again. These
decisions apply:

- Use Nemotron only, in bf16 only.
- Attach LoRA to attention `q/k/v/o_proj` and to Mamba `in_proj`. Do not attach LoRA
  to the experts.
- The engine label is `python-hf`.
- The reference weights are read-only.
- Apply `pin_layer_devices` on each path that operates a model on 2 GPUs.
- If a failure occurs, stop and tell the operator.

The input is the converted data of the STE batches. The reports are
`reports/2026-11-01-ste-batch-200.md` and `reports/2026-11-01-ste-batch-200-2.md`.
There are 387 trajectories in 2 files. Each file has a run record that holds its
sha256. A third batch is possible. The code reads one or more `converted.jsonl` files.
The quantity of files can change.

The scope has one exception to "Nemotron only". A render-only data record
(BUILD-RENDER-ONLY) can refer to a different model tree. This lets RECONCILE-EARLY
operate on a smaller model. A render-only data record does not load weights, and the
trainer does not use it.

These items are not in the scope:

- The SWE benchmark (section 7).
- An adapter that rewrites text.
- Changes to the STE conversion scripts.
- LoRA on the experts.

## 1. The function of the adapter

**PURPOSE-EMITTER.** The adapter writes STE100. The model operates as an agent of the
OpenHands type on a software task. With the adapter, the model writes its agent text in
STE100. The agent text is the text before each tool call and the `finish` message.
The tool calls must stay as correct as before. The training data shows this result. In
each trajectory, the conversion changed only the agent text. An adapter that rewrites
text from a different source is a different task. That task must have different data,
and this Spec does not include it. (Decision of the operator, 2026-11-02, section 10.)

**PURPOSE-MEASURE.** Measure the result of the adapter. Do not tell that it is good
without a measurement. The adapter is good when these 2 conditions apply:

1. Its STE pass rate on the held-out set (section 6) is higher than the pass rate of
   the model without an adapter.
2. Its correct-call rate does not decrease.

If the pass rate increases and the correct-call rate decreases, the result is a trade.
Tell the operator that it is a trade, not a good result.

## 2. The training data

A new command, `scripts/lora_build.py build`, writes the data for the trainer. In
this Spec, its name is the data script. The data script does not load model weights. It reads
only the tokenizer files of the model tree. Before it reads them, it makes sure that
the model tree agrees with its manifest.

**BUILD-INPUTS.** The data script reads one or more `converted.jsonl` files. For each file, it
reads the run record next to the file. If the sha256 of the file is not the same as the
`converted_sha256` of the record, the data script rejects the file. The data script
also rejects a row that is in 2 input files (the same dataset row number).

**BUILD-HELDOUT.** The data script reads the held-out set (section 6). If a uuid of an
input row is in the held-out set, the data script stops. The dataset uses one uuid for
more than one row. Thus, the data script compares uuids, not rows. A task in the
held-out set must not be in a row of the training data.

**BUILD-RENDER.** The data script applies the chat template of the model to each row,
with `apply_chat_template`. It gives the `tools` list of the row as `tools`. It sets
`enable_thinking` from an argument that the operator must supply. The data script
selects the same message fields as `ste_batch.TokenCounter`. It also changes the JSON
text of each tool-call argument into an object, as `ste_batch.TokenCounter` does. The
data record holds the template hash, the `tools` value and the `enable_thinking`
value. The score script rejects a data record with values that are different from the
values of the score script. It also rejects a data record with a `tree_hash` that is
different from the model that it loads. The `enable_thinking` value is `false`, and the `tools` list
goes to the template. (Decisions of the operator, 2026-11-02, section 10.)

**BUILD-LENGTH.** If a row has more tokens than `max_seq_length` after the template,
the data script removes the full row. The data record lists the row and its length.
The data script does not cut a row. The sample limits were 32,768 and 32,000 tokens,
without `tools`. Thus, some rows can become too long when the `tools` list is in the
template. The data record gives the count of these rows.

**BUILD-MASK.** Each row gets an `assistant_masks` array with the same length as its
`input_ids`. The value is 1 for each token that the assistant wrote. The value is 0
for all other tokens. The data script finds the assistant tokens from a difference
between 2 token sequences. For assistant message i, it applies the template 2 times:

1. To the messages before i, with `add_generation_prompt=True`.
2. To the messages before i and to message i.

The tokens that the second sequence adds are the tokens of the assistant. These
tokens are the content, the tool calls and the end-of-turn token. The header that
`add_generation_prompt` adds comes from the template, not from the assistant. Its
value stays 0.

**BUILD-PREFIX.** This method is correct only if each token sequence is the start of
the next token sequence. The data script examines this for each assistant message of
each row. At the first row where it is not correct, the data script stops. It tells the
operator the row and the message. It does not ignore the row, and it does not change
the method to continue. The Nemotron template has no `{% generation %}` markers. Thus,
TRL cannot find the assistant tokens, and the data script must find them.

**BUILD-ORDER.** The data script writes the rows in a random sequence from a seed. With
`--limit N`, it writes the first N rows of that sequence. Thus, the sets for the curve
(section 5) are in each other. The set of 100 rows is in the set of 200 rows. The
set of 200 rows is in the full set.

**BUILD-OUTPUT.** The data script writes 2 files. `train.jsonl` has one row on each
line, with `input_ids`, `assistant_masks`, and the batch, trajectory and dataset row
of the source. `build.json` is the data record. It holds these items:

- The input files and their sha256.
- The `tree_hash` of the model.
- The template hash, the `tools` value and the `enable_thinking` value.
- `max_seq_length`, the seed and the limit.
- The counts: rows in, rows removed and the cause, all tokens, assistant tokens, and
  the assistant tokens in converted STE text.

Then the data script writes a tree manifest of the output directory with
`weaver_train.manifest`. The output goes into `$WEAVER_TRAIN_STATE_DIR`, not into git.

**BUILD-RENDER-ONLY.** With `--render-only --model NAME`, the data script writes a
`build.json` and the token sequences for RECONCILE-RENDER. It does not write
`train.jsonl`. NAME can be a model in `config/models.toml` with a tree that agrees with
its manifest. A manifest of the tokenizer files only is sufficient, because a
render-only data record does not load weights. The trainer rejects a render-only data
record. A new model entry is a download, and the operator must give approval for it.
The operator gave approval for the tokenizer files of the 4B model on 2026-11-02, on
the 2 machines. Record which machine got which file, with the sha256 of each file.

## 3. Loss on the assistant tokens only

**LOSS-ASSISTANT.** Calculate the loss only on tokens with the value 1 in the mask. The
system tokens, the issue tokens, the tool output and the template tokens do not
change the loss. In batch 1, tool output was 57% of all tokens, and STE text was 5%.
Without this clause, most of the training is on text that the model does not write.

**LOSS-UNWEIGHTED.** Each assistant token has the same weight. STE text is
about 16% of the assistant tokens. The remaining tokens are tool calls, which
the model can write before the training. This Spec does not give more weight to STE text.
That is an open cell (section 10). Try it only if the curve stops its increase.

## 4. Changes to the trainer

`src/weaver_train/trainer.py` keeps its sequence of work. The sequence is: examine the
model tree, load, attach, train, save the adapter only, examine the model tree again,
write the run record. These clauses add to that sequence.

**TRAIN-TOKENIZED.** A new config key, `data.format`, is necessary. Its value is
`"text"` or `"tokenized"`. With `"text"`, the trainer operates as before, with
`text_field`. With `"tokenized"`, the trainer reads `input_ids` and `assistant_masks`
from section 2. There is no default value. With `"tokenized"`, the trainer rejects
these items:

- A dataset without its `build.json`.
- A dataset with a manifest that does not agree with the files.
- A `max_seq_length` that is different from the value in the data record.
- A data record with a `tree_hash` that is different from the `tree_hash` of the
  model that the trainer loads. The token IDs must come from the tokenizer of that
  model.

**TRAIN-MASK-REACHES-LOSS.** With `"tokenized"`, the labels that go to the loss must
agree with the mask. Where the mask is 0, the label is -100. Where the mask is 1, the
label is the same as the `input_ids` value. The code can use the `assistant_masks`
function of TRL or a new collator. With the 2 methods, the test in section 9 must
show the correct labels. If the code ignores the mask, the training uses all tokens,
and no warning occurs.

**TRAIN-NO-PACKING.** Do not use packing. Each row is one sequence. The value of
`per_device_batch_size` is 1. The trainer does not divide a row.

**TRAIN-EPOCHS.** The `[train]` table must have one of `max_steps` or
`num_train_epochs`. If it has the 2 keys or no key, the trainer gives a config error.

**TRAIN-EPOCH-ADAPTERS.** With `num_train_epochs`, the trainer saves the adapter at the
end of each epoch, in `adapter-epoch-N`. The directory holds adapter files only, as
`check_adapter_only` makes sure. The trainer also saves the last adapter as `adapter`.

**TRAIN-RECORD.** The run record also holds these items:

- The sha256 of `build.json` and the quantity of rows in the training.
- The quantity of assistant tokens.
- The steps and the epochs.
- The time for each step and the full time.
- The maximum memory on each GPU.
- The loss at each log step and the last loss.

The stop rules of the run sequence read these values.

**TRAIN-STOP.** The trainer stops and writes the status `failed` when one of these
conditions occurs:

- The loss is `NaN` or `inf`.
- The GPU memory is not sufficient.
- A `TargetCoverageError` occurs.
- A layer goes to the CPU.

The trainer does not try again with different values.

## 5. The sequence of runs

**RUN-CONFIG.** The first values are in this table. They are the same for all the
runs of the curve.

| Key | Value |
|---|---|
| `lora.r` / `lora.alpha` / `lora.dropout` | 16 / 32 / 0.0 |
| `lora.target_modules` | `q_proj`, `k_proj`, `v_proj`, `o_proj`, `in_proj` |
| `train.max_seq_length` | 32768 |
| `train.learning_rate` | 1e-4, cosine schedule, 10 warmup steps |
| `train.per_device_batch_size` / `gradient_accumulation_steps` | 1 / 8 |
| `train.num_train_epochs` | 2 |
| `train.seed` | 0 |

With 387 rows, one epoch is about 48 steps. These values are a first
estimate. The curve measures them. (The operator must give a decision before a change
to a value, section 10.)

**RUN-ORDER.** Do the runs in this sequence. Do not start a run before the previous
run is complete.

1. **R0, the baseline.** Use the score script on the model without an adapter.
2. **R1, 100 rows.** This run measures time and makes sure that the method operates.
   After R1, stop. Tell the operator the time, the maximum memory, the loss curve and
   the score. Do not start a longer run before this report.
3. **R2, 200 rows**, then **R3, all rows**. Use the sets of BUILD-ORDER.

Use the score script on the adapter of each epoch of each run. The curve shows 2 rates on the
held-out set: the STE pass rate and the correct-call rate. It shows them for each
quantity of training rows, at epoch 1 and at epoch 2.

**RUN-MARGIN.** This Spec sets the margin before R0. Do not change the margin after a
score is known. The curve compares 2 scores of the same segments each time. Thus, it
compares pairs. For 2 scores A and B:

- `b` is the quantity of segments that the checker accepts with B and rejects with A.
- `c` is the quantity of segments that the checker accepts with A and rejects with B.

B is higher than A only when these 2 conditions apply:

1. An `exact two-sided sign test` on `b` and `c` gives `p` less than 0.05.
2. The difference in pass rate is larger than the decode difference of SCORE-SPREAD.

If one condition does not apply, the name of the result is "flat". Use the same rule
for the correct-call rate. On 100 segments, a difference of less than about 10 points
usually shows as flat. This is a limit of the held-out set. Record the limit with
each result. Do not make the margin smaller because of it.

The 2 types of noise are different. SCORE-SPREAD measures the difference between 2
runs that are the same. The sign test measures the error that comes from a sample of
100 segments.

**RUN-READ.** Read the curve with RUN-MARGIN:

- If R3 is higher than R2, more data can help.
- If R3 and R2 are flat, more data does not help. Then the next change is the open cell
  of LOSS-UNWEIGHTED.
- If the correct-call rate of an adapter is lower than the rate of R0, tell the
  operator. The adapter then decreases the skill of the model.

**RUN-OUTPUT.** Each run writes to a new directory that is not in the model tree, as
the trainer makes sure. Put the adapters, the data records and the score files in a
directory in `weaver-testing/` on the `shared bulk store`. Add a `SHA256SUMS` file and
a README. Only the result note goes into git.

## 6. The score script for the held-out set

A new command, `scripts/lora_score.py`, gives the score of a model, with an adapter or
without an adapter, on the held-out set. In this Spec, its name is the score script. It loads
the model on the same path as the loader, after the model tree agrees with its
manifest. It applies `pin_layer_devices`. Its engine label is `python-hf`.

**SCORE-HELDOUT.** The held-out set is the kit of 100 segments. 90 segments are text
before a tool call. 10 segments are `finish` messages. The kit is frozen since 2026-11-01. No uuid of
the kit is in an STE batch. The kit also holds the decisions of the reviewers on the
Gemini and DeepSeek rewrites. These decisions stay as a reference. The score of the
model without an adapter is the baseline.

**SCORE-LOCATE.** For each segment, the kit holds the uuid, the location (message
index and field) and the original text. It does not hold the dataset row. The score
script finds the row with the same uuid and the same text at that location. If no row
or more than one row agrees, the score record lists the segment. The score script does
not give a score to that segment.

**SCORE-PREFIX.** For a segment at message i, the prompt is the messages before i. The
score script applies the template as BUILD-RENDER does, with
`add_generation_prompt=True`. The output limit is 2,048 tokens. If the prompt and the
output limit are more than `max_seq_length`, the score record lists the segment. The
score script does not give a score to that segment.

**SCORE-GENERATE.** The model writes its output with greedy decode, without a random
sample. The output limit is 2,048 tokens. The output stops at the end-of-turn token.
Greedy decode gives the same score again, and the curve must have that.

**SCORE-EXTRACT.** For a segment of text before a tool call, the score script uses the
content of the output message before the first tool call. For a `finish` message, it uses
the `message` argument of a `finish` call in the output. If the output has no such
text and the original had it, the score record counts it as "no text". "No text" is
not a pass.

**SCORE-CHECK.** The score script replaces code in the text with placeholders, with
`ste_convert.mask`. Then it examines the text with the same checker and prompt hash as
the conversion: `ste_experiment.Judge`, prompt `e21370fcd140`. For each segment, the score record holds
the result and the rules that the text does not obey. It also holds the count of -ing
words and the count of words. It also holds the totals.

**SCORE-VALIDITY.** Until section 7 has code, the correct-call rate is the measure of
skill. A tool call in the output is correct when these 3 conditions apply:

1. Its arguments are correct JSON.
2. It names a tool in the `tools` list of the row.
3. Its arguments include all the required parameters of that tool.

These 3 conditions examine only the JSON, the tool name and the parameters of the
tool call. The rate does not compare
the tool call with the original tool call of the segment. SCORE-REVIEW examines if
the next action is correct.

The call set is the held-out segments where the original message has a tool call.
SCORE-LOCATE and SCORE-PREFIX can list a segment and give it no score. Their results
come from the kit and the data record only, not from the model. Thus, before R0, the
score script removes these segments from the call set and from the STE pass rate. The
score record lists the removed segments. The same segments are removed for all runs.
For a `finish` message, the `finish` call is that tool call. The call set is the same
for all runs, and the score record lists it. For each segment of the call set, the
result is correct or incorrect. If the output has no tool call, the result is
incorrect. If the output has more than one tool call, each tool call must be correct.
The correct-call rate is the quantity of correct results, divided by the quantity of
segments in the call set. Thus, 2 runs give results for the same segments, and
RUN-MARGIN can compare them in pairs.

**SCORE-REVIEW.** For each adapter and for R0, a reviewer reads 30 segments. A seed
selects the 30 segments, and the selection does not change. The reviewer reads each
segment with its prompt and gives one decision: `fits`, `odd` or `incorrect`. The reviewer
examines 2 items: Is the text correct for the work of the agent? Is the next action
correct? The
checker cannot find these problems, as the conversion batches showed.

The reviewer is a Claude model, `claude-opus-5-5`, with the reviewer prompt
`ste/review-prompt.md`. That file goes into git with this code. It holds no text from
the standard. The score record holds the model ID and the sha256 of the reviewer
prompt, as SCORE-CHECK records the prompt hash of the checker. The reviewer does not
know which model wrote a segment. The segments of all the runs that the curve compares go
into one random sequence. Gemini and DeepSeek are not reviewers, because they wrote training
data or reference data. No text from the reviewer goes into training data. The
decisions of the reviewer are only for the score record. The operator can read a segment. The decision of the operator
then replaces the decision of the model, with a mark that shows this.

**SCORE-SPREAD.** Do R0 2 times. Greedy decode on a model on 2 GPUs can give a different
result on a second run. The difference between the 2 scores of R0 is the decode
difference that RUN-MARGIN uses.

**SCORE-OUTPUT.** For each segment, the score script writes one line of JSON. The line
holds the prompt length, the output text, the extracted text, the checker result and
the correct-call result. `score.json` is the score record. It holds these items:

- The `tree_hash` of the model.
- The sha256 of the adapter, or "none".
- The template hash and the `enable_thinking` value of the data record.
- The totals.

Do not put the results of one engine label together with the results of a different
engine label.

## 7. The SWE benchmark

**BENCH-DEFERRED.** The frozen benchmark snapshots must have a Docker harness. There is no
harness at this time. This Spec does not give approval for benchmark code. Until the
harness is available, only SCORE-VALIDITY and SCORE-REVIEW measure the skill. Do not
tell that an adapter keeps or decreases the SWE skill of the model. When the harness is
available, compare the models with and without an adapter as
`docs/benchmarks.md` tells.

## 8. The RECONCILE step and the SPU loop

A second machine makes the STE conversion loop and makes sure that it operates. The
loop operates through the SPU, as the first agent of WeaverAgent (`toddwbucy/WeaverAgent`
#430 and #431). At the same time, this machine makes the adapter. The 2 parts will not
be complete at the same time. Work on the training side continues while the second
machine makes the loop. Thus, a RECONCILE step is necessary, and this Spec gives it a
name. The only connection between the 2 sides is the data record of section 2. No
code, fixture or path connects this repository to WeaverAgent.

**RECONCILE-STEP.** The RECONCILE step occurs between these 2 points of the work:

1. The loop operates from start to end on the second machine.
2. The loop operates with the adapter of this Spec on this machine.

The step has the 3 checks below. Do not operate the loop with the adapter before the
3 results are in the record of RECONCILE-RECORD.

**RECONCILE-FIELDS.** The SPU side must agree with these fields of the `build.json`
that the adapter came from:

- The `tree_hash` of the model. The tokenizer comes from this tree.
- The template hash.
- The `tools` value.
- The `enable_thinking` value.
- `max_seq_length`.

BUILD-RENDER and BUILD-OUTPUT record these fields at this time. This clause adds no
field. If one of these fields changes after the RECONCILE step, do the RECONCILE step
again.

**RECONCILE-RENDER.** The training uses Python, and the SPU does not train. But the SPU
must give the model the same tokens that the trainer gave. Use 2 prompts. Apply the
template to each prompt with 2 methods:

- With BUILD-RENDER and the values of the data record.
- With the family entry of the model in the SPU.

Then compare the 2 token ID sequences of each prompt. These are the 2 prompts:

1. **The trajectory turn.** One held-out prompt, as SCORE-PREFIX makes it. The `tools`
   list of the row goes in, as the data record tells.
2. **The loop turn.** The turn that step 5 of the loop sends (decision P of the #431
   brief). The system message is the rules prompt `e21370fcd140`. The user message is
   one kit segment, with placeholders for the code. There is no `tools` list. This
   turn goes through a different path of the chat template. The loop sends this turn
   for all decisions about PURPOSE-EMITTER.

Write the message list of each prompt one time, as JSON. The 2 sides use the same
list. Thus, the 2 sides compare the template and not 2 methods that make the
messages. The message list of the loop turn holds text from the standard. Thus, it
goes only into the RECONCILE-RECORD directory, not into git.

If the 2 token sequences of a prompt are the same, the adapter gets the same tokens in
service as in training. If they are different, change the SPU side to agree with the
data record. Do not change the data record to agree with the SPU. The GGUF holds the
chat template in its metadata. Thus, the 2 sequences are usually the same. But
measure them. Do not think that they are the same without a measurement.

**RECONCILE-ADAPTER.** Change the bf16 PEFT adapter into a GGUF LoRA that the SPU can
load. The SPU cannot load a LoRA at this time. That work is for WeaverAgent (issue 4
of the #431 brief). This Spec supplies the adapter. Each adapter must agree with
TRAIN-EPOCH-ADAPTERS and must have a recorded sha256.

**RECONCILE-DRIFT.** The training side can change while the second machine makes the
loop. Compare each change with the values that the loop uses: the template,
`enable_thinking`, the `tools` value and `max_seq_length`. For each difference, use
the rule of RECONCILE-RENDER. The data record is the reference. The only exception is
a decision that changes the data record. Then the trainer makes a new adapter with
the new data record.

**RECONCILE-EARLY.** The second machine can do RECONCILE-RENDER before this machine is
free. It uses the Nemotron-3-Nano-4B GGUF and a render-only data record
(BUILD-RENDER-ONLY) for that model. This step has 2 conditions:

1. The data record uses the model entry of the 4B model.
2. The second machine holds the `Hugging Face` tokenizer files of the 4B model next to
   its GGUF. The files must have the same revision as the source of the GGUF. The GGUF
   cannot give a BUILD-RENDER sequence without these files.

This step shows that the method operates. Only this machine can show it for the 30B
A3B model.

**RECONCILE-RECORD.** Each RECONCILE step has one directory in `weaver-testing/` on
the `shared bulk store`. Its name is `lora-reconcile-<date>-<model>-<first 8 of the adapter
sha256>`. For RECONCILE-EARLY, the last part of the name is `none`. The directory has
a `SHA256SUMS` file and a README, as in RUN-OUTPUT. It holds these items:

- The `build.json` of the adapter, or the render-only data record.
- The 2 message lists.
- The token ID sequences of the 2 sides for each prompt, and the result when the
  2 sides compare them.
- The sha256 of the PEFT adapter and the sha256 of the GGUF LoRA.
- The table of RECONCILE-DRIFT.

The 2 repositories refer to this directory by its name. Thus, each side has the record
and does not use only the word of the other side.

## 9. Tests

Each clause has a test in `tests/`. Where a test must have a model, it uses the small
model of `tests/conftest.py`.

- **BUILD-INPUTS, BUILD-HELDOUT:** The data script rejects a converted file with a
  sha256 that is different from its run record. It rejects a row with a uuid from the
  held-out set.
- **BUILD-MASK, BUILD-PREFIX:** Use fixture rows with tool calls. The tokens with mask
  1 are the same as the assistant messages after the template. No system, user or tool
  token has the mask value 1. If a template does not keep each sequence as the start of the
  next, the data script stops.
- **BUILD-LENGTH, BUILD-ORDER:** The data script removes a row that is too long and
  lists it. The sets of `--limit` are in each other.
- **TRAIN-TOKENIZED, TRAIN-EPOCHS:** The config errors occur. The trainer rejects a
  data record with a `tree_hash` that is different from the model tree.
- **TRAIN-MASK-REACHES-LOSS:** Do one training step on the small model. Record the
  labels at the loss. The label is -100 at each token with mask 0, and only there.
  This test uses the GPU.
- **TRAIN-EPOCH-ADAPTERS:** A run of 2 epochs on the small model gives 2 epoch adapters
  and one last adapter. The directories hold adapter files only.
- **SCORE-LOCATE, SCORE-EXTRACT, SCORE-VALIDITY:** The score record lists rows that
  are not found and rows that more than one row agrees with. The score script gets
  the text and the `finish` message. It counts tool calls that are not correct. In
  the call set, an output with no tool call is incorrect, and the call set is the same
  for 2 runs. A segment that SCORE-LOCATE or SCORE-PREFIX lists is not in the call set
  or in the STE pass rate, and the score record lists it.
- **RECONCILE-FIELDS:** `build.json` holds each field of the clause. The score script
  rejects a data record without one of the fields. The RECONCILE step is a record of
  work on the model files, not a unit test.
- **BUILD-RENDER-ONLY:** A render-only data record with the tokenizer of the small
  model has `build.json` and the 2 token sequences, and no `train.jsonl`. The trainer
  rejects it.
- **RUN-MARGIN:** Use test vectors of accept and reject results. The sign test and the
  difference rule give the correct "higher" and "flat" results. One case has a sign test
  with p less than 0.05 and a difference that is smaller than the decode difference.
  That case is flat.
- **SCORE-REVIEW:** The score record holds the sha256 of the reviewer prompt and the
  model ID. The random selection does not show which run wrote a segment.
- `tests/test_independence.py` continues to agree with the code. The 2 new commands
  are scripts, and scripts can import scripts. A module in `src/` imports no
  different module, but it can import `manifest`.

## 10. Decisions and open cells

**Adapter function (PURPOSE-EMITTER).** Closed. Decision of the operator, 2026-11-02:
the adapter writes STE100. This Spec does not make an adapter that rewrites text.
Decision J of WeaverAgent recommended an adapter that rewrites text. This decision
closes that difference on this side. The first local run of the STE conversion loop
uses this adapter for step 5. An open cell of the loop
(`toddwbucy/WeaverAgent#431`, section 11) measures the output format of the adapter. That
cell shows if this adapter can also do the turn that rewrites text.

**The `enable_thinking` value (BUILD-RENDER).** Open. Decision of the operator,
2026-11-02: `enable_thinking` is `false` in the data script and in the score script,
for this Spec. The template can hold `reasoning_content`. The data has no
`reasoning_content`. The model writes reasoning text only in the calls of the
`think` tool. The think-scope decision continues to apply with this value. The cause of this decision: an adapter that rewrites text possibly must
have the value `true`. An adapter that writes STE100 possibly operates more
correctly with `false`. A new decision can try the value `true` with an adapter that rewrites
text. Until that decision, this cell stays open.

**Tools in the template (BUILD-RENDER).** Closed. Decision of the operator,
2026-11-02: the `tools` list goes to the template. Then the model gets the tool
schemas, as it does when it operates as an agent. The data record continues to hold
the count of rows that BUILD-LENGTH removes. If the `tools` list causes the removal of
many rows, the operator gives a new decision.

**Start without the benchmark (BENCH-DEFERRED).** Closed. Decision of the operator,
2026-11-02: the training starts before the harness is available. Until the harness is
available, only SCORE-VALIDITY and SCORE-REVIEW measure the skill.

**The RECONCILE token check (RECONCILE-RENDER).** The 2 token ID checks close this
cell: the trajectory turn and the loop turn. Do them first on the 4B GGUF on the
second machine (RECONCILE-EARLY). Then do them on the 30B A3B model on this machine.

**The 4B tokenizer files (RECONCILE-EARLY).** Closed. Decision of the operator,
2026-11-02: add the model entry of Nemotron-3-Nano-4B and get its `Hugging Face`
tokenizer files. Use the revision of the source of the GGUF. The approval is for the
2 machines. Record which machine got which file, with the sha256 of each file.

**The run values (RUN-CONFIG).** The curve closes this cell. If the loss of R1 does
not decrease by its second epoch, the operator gives a decision about the learning
rate before R2.

**More weight on STE text (LOSS-UNWEIGHTED).** RUN-READ closes this cell. Try it only
if R3 and R2 are flat.

**Sufficient data.** RUN-READ closes this cell with R2 and R3. If there is a third STE
batch, keep it, unless R3 is higher than R2.

**The 32K limit.** All training rows have 32K tokens or less. About one dataset
row in 10 has this length. A held-out set of longer trajectories is necessary
to show if STE text continues on longer tasks. That set must have a larger
`max_seq_length`. Before that, a memory test at 64K must occur.
