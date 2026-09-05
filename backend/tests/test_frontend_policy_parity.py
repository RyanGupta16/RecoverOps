"""The policy table on the website must be the policy the backend enforces.

These are two hand-maintained lists in two languages, and they drifted: the
site advertised twelve rules while the gate ran twenty-nine, so the whole v2
compliance layer — promises, degradation, mandates, voice, receivables — was
enforced but invisible to anyone reading the site. A demo that says one number
while the code says another is the kind of thing a reviewer finds first.

Parsing TypeScript from Python is crude, but the alternative is a build step
for one array, and drift is the failure mode that actually happened.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.policy import RULES

POLICY_TS = Path(__file__).resolve().parent.parent.parent / "src" / "lib" / "policy.ts"

pytestmark = pytest.mark.skipif(not POLICY_TS.exists(), reason="frontend not present")


def _frontend_rules() -> list[dict]:
    block = re.search(
        r"export const POLICY_RULES: PolicyRule\[\] = \[(.*?)\n\];", POLICY_TS.read_text(), re.S
    )
    assert block, "POLICY_RULES array not found in src/lib/policy.ts"
    out = []
    for entry in re.finditer(r"\{(.*?)\n  \}", block.group(1), re.S):
        body = entry.group(1)

        def field(name: str) -> str | None:
            m = re.search(rf"{name}:\s*\n?\s*'((?:[^'\\]|\\.)*)'", body)
            return m.group(1).replace("\\'", "'").replace("\\\\", "\\") if m else None

        out.append(
            {
                "id": field("id"),
                "description": field("description"),
                "citation": field("citation"),
                "category": field("category"),
            }
        )
    return out


def _normalise(s: str | None) -> str | None:
    """The site uses typographic apostrophes; the backend uses ASCII."""
    return s.replace("’", "'") if s else s


def test_the_website_lists_every_rule_the_gate_enforces():
    fe = {r["id"] for r in _frontend_rules()}
    be = {r.id for r in RULES}
    assert fe == be, (
        f"enforced but not shown: {sorted(be - fe)}; shown but not enforced: {sorted(fe - be)}"
    )


def test_rules_appear_in_evaluation_order():
    """Order is meaningful — the first block stops evaluation — so a reader of
    the table sees the same precedence the gate applies."""
    assert [r["id"] for r in _frontend_rules()] == [r.id for r in RULES]


def test_descriptions_citations_and_categories_match():
    fe = {r["id"]: r for r in _frontend_rules()}
    for rule in RULES:
        f = fe[rule.id]
        assert _normalise(f["description"]) == rule.description, f"{rule.id} description drifted"
        assert _normalise(f["citation"]) == rule.citation, f"{rule.id} citation drifted"
        assert f["category"] == rule.category, f"{rule.id} category drifted"


def test_only_rules_with_a_real_citation_carry_one():
    """The site says citations mark actual published rules. A product policy
    dressed as regulation would be the worst kind of error on this page."""
    cited = [r for r in _frontend_rules() if r["citation"]]
    assert len(cited) == len([r for r in RULES if r.citation]) == 18
    for r in cited:
        assert not re.match(r"^(our|internal|product)\b", r["citation"], re.I)
