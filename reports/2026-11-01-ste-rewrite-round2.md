# STE100 rewriter selection: repair round and meaning review (2026-11-01)

Follows `2026-11-01-ste-rewriter-selection.md`, which describes the test. Result files
are in `2026-11-01-ste-rewrite-round2/`.

## What ran

The repair round was sent at 14:14Z on 2026-11-01 to DeepSeek V4 Pro, Gemini 4 Argon
and Kimi K3. Each opencode session was cleared first, so a model had only the files.

- Each model got its failed round 1 segments: 45 for DeepSeek, 29 for Gemini and 47
  for Kimi.
- Each failed segment came with the original text, the model's rewrite and the
  checker's notes.
- All three finished by 14:37Z.

The final rewrite of a segment is the model's round 2 output where there is one, and
its round 1 output where there is not.

## Checker results

| Model | Round 1 | After repair | issue (15) | narration (30) | think (15) | Still failing |
|---|---|---|---|---|---|---|
| Gemini 4 Argon | 31/60 | **58/60** | 14 | 30 | 14 | length 2 |
| DeepSeek V4 Pro | 15/60 | 57/60 | 13 | 30 | 14 | verbs 2, -ing 1, length 1 |
| Kimi K3 | 13/60 | 57/60 | 14 | 30 | 13 | length 2, verbs 1 |

With the checker's notes, all three models went from blind scores to 95% or better.
For each model, all 30 narration segments now pass.

## The checker caused data damage

Two issue segments, s32 and s38, hold text that the masking step should have replaced
with placeholders, and did not:

- HTML `<details>` and `<summary>` tags;
- a pandas "installed versions" listing, which is program output.

The checker flagged "details" as an unapproved verb and "Stepping" (in
`Intel64 Family 6 Model 158 Stepping 10`) as an -ing form.

- **Gemini and Kimi obeyed the checker.** They removed the tags and changed the
  processor line to `Step 10`. Both changes corrupt the data, and the checker now
  passes both segments.
- **DeepSeek refused.** It kept the tags and the program output unchanged, and its
  report says why: they are markup and technical data. So 2 of its 3 remaining
  checker failures are correct behavior.

The same gap lets quoted error messages through unmasked. In s52 and s55, all three
models reworded a Python error message. For example, "codec can't encode character"
became "codec cannot encode the character".

## Meaning review

There was one reviewer per model, each a separate Claude instance. Each read 40
segments: all issue and `think` segments, and the narration segments over 500
characters. The reviewers judged meaning only, not STE style. Each finding quoted
below was checked against the files.

| Model | Faithful | Minor drift | Wrong | Data altered | Total cost |
|---|---|---|---|---|---|
| DeepSeek V4 Pro | 15 | 19 | 4 | 2 | $2.74 |
| Gemini 4 Argon | 17 | 15 | 2 | 6 | $1.90 |
| Kimi K3 | 14 | 18 | 4 | 4 | $3.28 |

The three reviewers were not calibrated against each other, so read differences of
one or two as noise. Two segments show this:

- s21 was judged "wrong" for DeepSeek and Gemini, but "minor drift" for Kimi.
- s34 ("ordered Categorical" became "sorted Categorical", a different pandas property)
  is wrong for DeepSeek and Gemini. Kimi kept "ordered".

Data damage counts are more reliable, because each instance is a literal string that
changed:

| Model | Data damage |
|---|---|
| DeepSeek V4 Pro | error messages only (s52, s55) |
| Gemini 4 Argon | the same two error messages; s32 and s38 (tags, program output); words inserted into literal strings next to placeholders (s42 `145.[[C8]]` became `145. with [[C8]]`; s50, a regex character list) |
| Kimi K3 | the same two error messages; s32 and s38 |

Wrong meaning, by model:

| Model | Segments with wrong meaning |
|---|---|
| DeepSeek V4 Pro | s21 ("dispose" became "remove"); s27 ("underlying error" became "correct error"); s34; s43 (adds "URL-encoded") |
| Gemini 4 Argon | s21 (a key leak became keys that stay in the bucket); s34 |
| Kimi K3 | s25, s35, s60: "check if X" became "make sure that X", so a test became an assertion. In s60, "check if we're in the final iteration" became "make sure that the iteration is the last". s29: "indexes rows" became "uses the rows as the index". |

Patterns that recur in all three models:

- **Hedges become facts.** "suggests" and "seems" became "shows", and "should
  probably" became "must". This is the largest source of drift, and our thesaurus
  guidance makes it worse: it maps "should" to "must" without a hedge sense for
  "suggests".
- **"route" became "path"** in web-routing text.
- **"reindex" became "make a new index".**
- **"fail" became "give an error"**, which blurs the difference between a test that
  fails and a test that errors in pytest.
- Kimi only:
  - "original" became "first" and "root cause" became "first cause", which implies an
    order that is not there;
  - "expects" became "waits for".

## What this means

1. **The checker's pass rate gives a much better picture than the real one.** At
   57 to 58 of 60 passing, the most faithful model still has drift in about half of
   its long segments. Meaning must be judged on its own; the checker cannot see it.
2. **Some of the faults are ours, not the models':**
   - the masking step misses HTML tags, program output listings and quoted error
     messages;
   - the checker judges words inside them;
   - the rules give no approved way to keep a hedge ("suggests", "seems");
   - "check if" has no approved form that keeps the sense of a test.

   Correcting these comes before any larger run.
3. **Choosing between the models.** Gemini is the cheapest ($0.034 per checker pass)
   and has the fewest wrong-meaning segments, but it damaged the most data. DeepSeek
   is the most careful with data and refused a wrong checker note, at about 1.4x
   Gemini's cost. Kimi costs the most and its "check if" pattern changes meaning. No
   model is chosen yet: the next run will use the corrected masking, checker and rules
   above.

## Pending

- Correct the masking step and the checker:
  - mask HTML tags, program output listings and quoted error messages;
  - do not judge words inside masked text.
- Add hedge and "check if" guidance to the rules and the thesaurus, and run a second
  kit round on the same segments.
- A meaning judge that does not depend on the reviewer: Jev is on hold until the
  operator's go.
- The operator's rulings listed in `2026-11-01-ste-rewriter-selection.md`.

Note: the Kimi repair cost ($1.64) is the same as its round 1 cost. It is recorded as
the operator read it from the dashboard.

## Files in `2026-11-01-ste-rewrite-round2/`

| File | What it is |
|---|---|
| `<model>.round2.jsonl` | the repaired rewrites (`id`, `output`), only for the segments that failed round 1 |
| `<model>.score.md` | the checker result for each segment, with rounds 1 and 2 merged |
| `<model>.repair1-report.txt` | the model's own `.done` report, unchanged |
| `<model>.meaning.md` | the reviewer's full meaning review |

The repair notes (`repair-1.jsonl`) are not included: they quote approved alternatives
from the standard.
