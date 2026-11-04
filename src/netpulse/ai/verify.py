"""Number verification: every figure in the narrative must come from the facts.

LLMs are fluent and confidently wrong with numbers. Before any generated text
reaches the report we extract each number it contains and look for a matching
number in the fact sheet (tolerating rounding: "967 Mbit/s" matches
"966.86 Mbit/s", "6%" matches 6.25). Identifiers (INC-03), clock times, dates
and small counting numbers are not claims and are skipped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

NUM = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?")
SKIP = [
    re.compile(r"\bINC-\d+\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),
    re.compile(r"\b[a-z]{2,}-\d+(?:/\d+)+\b", re.I),  # interface names like xe-0/0/1
    re.compile(r"\b(?:ge|xe|et|eth)-?\d+(?:/\d+)*\b", re.I),
    re.compile(r"\b[\w-]+-\d+\b"),  # device names like core-rtr-1, edge-fw-1
    re.compile(r"\b(?:p|P)\d{2}\b"),  # p95, P99
]


def numbers_in(text: str) -> list[float]:
    t = text
    for pat in SKIP:
        t = pat.sub(" ", t)
    out = []
    for m in NUM.finditer(t):
        s = m.group(0).replace(",", "")
        try:
            out.append(float(s))
        except ValueError:
            continue
    return out


def fact_numbers(facts) -> list[float]:
    vals: list[float] = []

    def walk(x):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            vals.append(float(x))
        elif isinstance(x, str):
            vals.extend(numbers_in(x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    walk(facts)
    return vals


def _decimals(x: float) -> int:
    s = f"{x}"
    return len(s.split(".")[1].rstrip("0")) if "." in s else 0


def matches(claim: float, known: list[float], rel_tol: float = 0.03) -> bool:
    half_unit = 0.5 * 10 ** (-_decimals(claim)) if _decimals(claim) else 0.5
    return any(abs(claim - k) <= max(rel_tol * abs(k), half_unit) for k in known)


@dataclass
class Verification:
    checked: int
    unverified: list[float]

    @property
    def ok(self) -> bool:
        return not self.unverified


def verify_text(text: str, facts, small_ints: int = 12) -> Verification:
    known = fact_numbers(facts)
    bad = []
    nums = numbers_in(text)
    for n in nums:
        if n.is_integer() and 0 <= n <= small_ints:
            continue  # "two links", "3 incidents", list numbering
        if not matches(n, known):
            bad.append(n)
    return Verification(len(nums), bad)


def verify_payload(payload: dict, facts) -> Verification:
    return verify_text(json.dumps(payload, ensure_ascii=False), facts)


SENTENCE = re.compile(r"(?<=[.!?])\s+")


def drop_unverified_sentences(text: str, facts) -> tuple[str, int]:
    """Remove sentences that contain numbers we cannot verify."""
    kept, dropped = [], 0
    for sent in SENTENCE.split(text.strip()):
        if sent and not verify_text(sent, facts).ok:
            dropped += 1
            continue
        kept.append(sent)
    return " ".join(kept), dropped
