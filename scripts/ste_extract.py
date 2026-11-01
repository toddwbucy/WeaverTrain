"""Extract the approved verbs (headword, forms, meanings) from the operator's ASD-STE100 copy.

    python scripts/ste_extract.py [--out var/ste/approved-verbs.json]

Reads $WEAVER_TRAIN_STE100_SOURCE (the Issue 9 PDF), checks its sha256 against the
reference in ste/ste100-scheme.candidate.json, converts it with `pdftotext -layout` and
parses Part 2. The output holds text from the standard, so it is written under var/
(gitignored) and never committed: ASD reserves reproduction and publication.

A census, not the checked extraction O02-1 owes: the headword count must equal the
208 that two independent extractions found (dictionary v0.6), or the script fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXPECT_VERBS = 208
HEAD = re.compile(r"^([A-Z][A-Z-]+(?: [A-Z]+)*) \(v\),?")
ENTRY = re.compile(r"^([A-Za-z][A-Za-z-]*(?: [A-Za-z]+)*) \((v|n|adj|adv|conj|prep|pron|art)\)")
FORM = re.compile(r"^([A-Z][A-Z-]+(?: [A-Z]+)*),?$")
NUM = re.compile(r"^(\d+)\.\s+")


def source_text() -> tuple[str, str]:
    src = Path(os.environ["WEAVER_TRAIN_STE100_SOURCE"])
    want = json.loads((REPO / "ste" / "ste100-scheme.candidate.json").read_text())["reference"]["copy_sha256"]
    got = hashlib.sha256(src.read_bytes()).hexdigest()
    if got != want:
        sys.exit(f"ASD source sha256 {got} != reference {want}")
    out = subprocess.run(["pdftotext", "-layout", str(src), "-"], check=True, capture_output=True, text=True)
    return out.stdout, got


def dictionary_lines(text: str) -> list[str]:
    lines = text.splitlines()
    # Part 2 word list: from the first approved entry "A (art)" to the end of Part 2
    start = next(i for i, l in enumerate(lines) if re.match(r"^A \(art\)", l))
    return lines[start:]


def cols(line: str) -> list[tuple[int, str]]:
    """(start offset, text) for each run separated by 2+ spaces."""
    return [(m.start(), m.group()) for m in re.finditer(r"\S+(?: \S+)*", line)]


# writer's notes inside the meaning column; what follows them shares lines with the
# non-STE example column, so only the approved words they name are kept
NOTE = re.compile(r"For other meanings|For [a-z ]+, use:|Do not use|Use this word|Also use")
CAPS = re.compile(r"^[^a-z]*[A-Z]{2,}[^a-z]*$|^[A-Z]$")


def strip_examples(text: str) -> str:
    """Drop the all-caps STE example words that share lines with the meaning.

    Keeps an all-caps word that names a dictionary entry, as in "use: START (v)"."""
    words = text.split()
    keep = []
    for k, w in enumerate(words):
        nxt = words[k + 1] if k + 1 < len(words) else ""
        if CAPS.match(w) and not re.match(r"^\((v|n|adj|adv|conj|prep|pron)\)", nxt) \
                and not NUM.match(w + " "):
            continue
        keep.append(w)
    return " ".join(keep)


def parse(lines: list[str]) -> list[dict]:
    verbs = []
    i = 0
    while i < len(lines):
        m = HEAD.match(lines[i])
        if not m:
            i += 1
            continue
        head = m.group(1)
        forms, body = [head], []
        j = i
        while j < len(lines):
            line = lines[j]
            if j > i and (ENTRY.match(line) or re.match(r"^[A-Za-z]+-\s", line) or line.startswith(("Issue 9", "Page ", "2025-01-15"))
                          or "ASD-STE100" in line):
                break
            row = cols(line[m.end():] if j == i else line)
            if j == i:
                row = [(o + m.end(), t) for o, t in row]
            elif row and row[0][0] == 0 and not re.search(r"[a-z]", row[0][1].replace("(also", "")):
                forms += re.findall(r"[A-Z][A-Z-]+(?: [A-Z]+)*", row[0][1].replace("(also", ""))
                row = row[1:]
            body.append([(o, t) for o, t in row if not 0 < o < 12])  # drop notes under the forms
            j += 1
        # the STE example column: the leftmost all-caps cell right of the meanings
        meaning = [t for r in body for o, t in r]
        text = " ".join(meaning)
        text = re.sub(r"\s+", " ", text).strip()
        text = strip_examples(text)
        senses, see = [], []
        for sense in (x.strip() for x in re.split(r"(?:^|\s)\d+\.\s+", text) if x.strip()):
            cut = NOTE.search(sense)
            if cut:
                see += re.findall(r"\b([A-Z][A-Z -]*[A-Z]) \((?:v|n|adj|adv)\)", sense[cut.start():])
                sense = sense[:cut.start()].strip()
            senses.append(sense)
        verbs.append({"key": head.lower(), "headword": head,
                      "forms": list(dict.fromkeys(f.lower() for f in forms if not f.endswith("-"))),
                      "meanings": senses, "see": list(dict.fromkeys(see))})
        i = j
    return verbs


UNAPPROVED_V = re.compile(r"^([a-z][a-z-]*(?: [a-z]+)*) \(v\)")
ALT = re.compile(r"([A-Z][A-Z-]*(?: [A-Z]+)*) \((v|n|adj|adv|prep|conj)\)")


def parse_unapproved_verbs(lines: list[str]) -> list[dict]:
    """Unapproved verbs and the approved words Part 2 gives for them."""
    out = []
    for i, line in enumerate(lines):
        m = UNAPPROVED_V.match(line)
        if not m:
            continue
        alts = []
        for j in range(i, min(i + 12, len(lines))):
            if j > i and (ENTRY.match(lines[j]) or HEAD.match(lines[j])):
                break
            alts += [f"{w} ({pos})" for w, pos in ALT.findall(lines[j][m.end() if j == i else 0:])]
        out.append({"word": m.group(1), "alternatives": list(dict.fromkeys(alts))})
    return out


WORD_HEAD = re.compile(r"^([A-Z][A-Za-z-]*(?: [A-Z][A-Za-z-]*)*) \((v|n|adj|adv|conj|prep|pron|art)\)")
WORD_LOW = re.compile(r"^([a-z][a-z-]*(?: [a-z]+)*) \((v|n|adj|adv|conj|prep|pron|art)\)")


def parse_words(lines: list[str]) -> tuple[dict, dict]:
    """Every approved word (headword or listed form, any part of speech) and every
    unapproved word, each mapped to its parts of speech."""
    approved: dict[str, set] = {}
    unapproved: dict[str, set] = {}
    for i, line in enumerate(lines):
        m = WORD_HEAD.match(line)
        if m and m.group(1).isupper():
            approved.setdefault(m.group(1).lower(), set()).add(m.group(2))
            for j in range(i + 1, min(i + 6, len(lines))):
                c = re.match(r"^((?:\(also )?[A-Z][A-Z-]+(?:, ?(?:\(also )?[A-Z][A-Z-]+\)?)*),?\)?(\s|$)", lines[j])
                if not c:
                    break
                for f in re.findall(r"[A-Z][A-Z-]+", c.group(1)):
                    approved.setdefault(f.lower(), set()).add(m.group(2) + " form")
            continue
        m = WORD_LOW.match(line)
        if m:
            unapproved.setdefault(m.group(1), set()).add(m.group(2))
    return ({k: sorted(v) for k, v in approved.items()}, {k: sorted(v) for k, v in unapproved.items()})


def technical_noun_examples(text: str) -> list[str]:
    """The example technical nouns rule 1.5 lists by category (the rule's own section)."""
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if re.match(r"^\s*Rule 1\.5\s", l)]
    stops = [i for i, l in enumerate(lines) if re.match(r"^\s*Rule 1\.6\s", l)]
    a, b = starts[-1], stops[-1]
    words = set()
    for l in lines[a:b]:
        l = l.strip()
        if "," in l and not l.endswith(":") and not re.match(r"^(Rule|Issue|Page|Terms|Note|Non-STE|STE)", l):
            for w in l.split(","):
                w = w.strip().rstrip(".").lower()
                if w and len(w) < 40 and re.fullmatch(r"[a-z0-9][a-z0-9 /&()'.-]*", w):
                    words.add(w)
    return sorted(words)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(REPO / "var" / "ste" / "approved-verbs.json"))
    a = ap.parse_args(argv)
    text, sha = source_text()
    verbs = parse(dictionary_lines(text))
    if len(verbs) != EXPECT_VERBS:
        sys.exit(f"found {len(verbs)} approved verb headwords, expected {EXPECT_VERBS}")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    unapproved = parse_unapproved_verbs(dictionary_lines(text))
    words, unwords = parse_words(dictionary_lines(text))
    out.write_text(json.dumps({"source_sha256": sha, "verbs": verbs, "unapproved_verbs": unapproved,
                               "approved_words": words, "unapproved_words": unwords,
                               "technical_noun_examples": technical_noun_examples(text)}, indent=1) + "\n")
    print(f"{len(verbs)} approved verbs, {len(unapproved)} unapproved verbs -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
