# STE100 usage notes for software text

Operator clarifications on how to write common software verbs that STE100 does not
approve. Candidate, unratified. Written in our own words; the approval status of each
word is from the operator's local copy of the standard, and no text of the standard is
copied here. The technical verbs we admit are in `technical-verbs.toml`.

## analyze (2026-11-01)

"analyze" is not an approved verb, and the standard gives no alternative: write the
sentence in a different way. Choose the approved word by what the analysis is for.

- **examine**: for analyzing code, an issue, a traceback or results, meaning looking at
  something carefully to understand it.
  - "Let me analyze the issue" -> "I will examine the issue."
  - "Analyzing the traceback shows..." -> "The traceback shows...", or "I examined the
    traceback. It shows..."
- **measure**: only for quantities such as time, memory or size.
  - "Analyze the performance" -> "Measure the time that the function uses."
- **find** and **identify** are approved and often fit the purpose of the analysis
  better.
  - "Analyze why the test fails" -> "Find the cause of the test failure."
- **inspection** (noun) is approved, so "do an inspection of the module" also works.
  "inspect" as a verb is not approved.
