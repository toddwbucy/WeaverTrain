# Worked examples for the STE rewriter

Each example becomes one user turn and one assistant turn after the system prompt. They
are written for this project in our own words, on purpose from kinds of project that
are not in the pilot data, so that the model learns the rules and does not copy text.
Each block is "## Input", the text, "## Output", the text, and the blocks are divided
by a line with "---".

---

## Input

Now let me look at the test file to see how the existing tests are structured.

## Output

Now I will examine the test file to find the structure of the tests in it.

---

## Input

The retry loop in [[C1]] never resets the counter, so after the first error every request is treated as a retry. I think the fix is to reset it inside [[C2]], but I need to make sure this doesn't break the timeout handling.

## Output

The retry loop in [[C1]] does not set the counter to zero again. Thus, after the first error, the loop counts each request as a retry. I think that the correct repair is to set the counter to zero in [[C2]]. But first, I must make sure that this change does not cause errors in the timeout logic.

---

## Input

When the config file contains an empty section, [[C1]] crashes with a KeyError instead of returning the default values.

[[C2]]

**Expected behavior:** empty sections should be ignored.

## Output

If the configuration file contains an empty section, [[C1]] stops and gives a KeyError. It does not give the default values.

[[C2]]

**Correct result:** The function ignores the empty sections.

---

## Input

### Step 3. Reproduce the bug

Let me run the reproduction script to confirm the error:

## Output

### Step 3. Cause the error again

I will start the script that causes the error. Then I will make sure that the error occurs:
