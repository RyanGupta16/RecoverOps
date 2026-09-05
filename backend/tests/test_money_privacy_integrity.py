"""Three properties a payments reviewer will check first: the money adds up,
the customer's details do not leak, and the record cannot be quietly altered.

These are written as invariants over real generated batches rather than over
hand-picked fixtures, so they hold across the whole distribution of inputs the
system actually sees.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import re

import pytest

from app.leaks import LeakEvent
from app.merchant import MerchantConfig
from app.runtime import Runtime
from app.store import Store


@pytest.fixture(scope="module")
def rt(tmp_path_factory):
    r = Runtime.build(store_path=tmp_path_factory.mktemp("mpi") / "ledger.db")
    r.run_and_store("simulator", seed=31, count=300)
    return r


@pytest.fixture(scope="module")
def batch(rt):
    return rt.store.get_batch(rt.store.latest_batch_id())


# ==================================================================== money


def _walk(node, path="$"):
    """Every scalar in a nested structure, with its path."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, node


def test_every_money_field_is_an_integer_number_of_paise(batch):
    """Money in floats is how rounding losses get into a ledger. Every field
    whose name ends in Paise must be a whole number of paise, everywhere in
    the payload, at any depth."""
    offenders = [
        (p, v) for p, v in _walk(batch)
        if p.split(".")[-1].split("[")[0].endswith("Paise") and not isinstance(v, int)
    ]
    assert not offenders, f"non-integer money: {offenders[:5]}"


def test_no_money_field_is_silently_negative_where_it_cannot_be(batch):
    non_negative = ("amountPaise", "recoveredPaise", "contactCostPaise", "atRiskPaise", "costPaise")
    bad = [
        (p, v) for p, v in _walk(batch)
        if p.split(".")[-1].split("[")[0] in non_negative and isinstance(v, int) and v < 0
    ]
    assert not bad, f"negative where impossible: {bad[:5]}"


def test_net_value_reconciles_exactly_from_its_components(batch):
    """net = recovered − channel cost − churn damage. If this drifts by even
    one paise the arithmetic is happening in floats somewhere."""
    for key in ("A", "B"):
        m = batch["agents"][key]["metrics"]
        expected = m["recoveredPaise"] - m["contactCostPaise"] - m["outreachCausedChurnLossPaise"]
        assert m["netValuePaise"] == expected, f"agent {key} net does not reconcile"


def test_channel_cost_equals_the_sum_of_the_decisions_that_incurred_it(rt, batch):
    """The headline cost must be the sum of the per-decision costs, not a
    separately computed number that can drift from them."""
    traces = {e["eventId"]: rt.store.get_trace(e["eventId"], batch["batchId"]) for e in batch["events"]}
    total = sum(t["agentB"].get("costPaise", 0) for t in traces.values() if t["agentB"]["chosenAction"] in
                {"payment_link_sms", "payment_link_whatsapp", "card_update_request", "incentive_link",
                 "invoice_reminder", "statement_of_account", "msmed_notice", "cart_reminder",
                 "cart_incentive", "voice_call", "virtual_account"})
    assert total == batch["agents"]["B"]["metrics"]["contactCostPaise"]


def test_recovered_amount_never_exceeds_the_amount_at_risk(batch):
    at_risk = sum(e["amountPaise"] for e in batch["events"])
    for key in ("A", "B"):
        assert batch["agents"][key]["metrics"]["recoveredPaise"] <= at_risk


def test_counts_are_consistent_with_the_event_rows(batch):
    for key in ("A", "B"):
        m = batch["agents"][key]["metrics"]
        agent = key.lower()
        contacted = sum(1 for e in batch["events"] if e[f"agent{key}"]["contacted"])
        recovered = sum(1 for e in batch["events"] if e[f"agent{key}"]["recovered"])
        assert m["contactsMade"] == contacted, f"{agent} contact count disagrees with the rows"
        assert m["recoveredCount"] == recovered, f"{agent} recovery count disagrees with the rows"
        assert m["eventsProcessed"] == len(batch["events"])
        assert m["contactsMade"] <= m["contactBudget"], "budget was exceeded"


def test_large_amounts_survive_without_precision_loss():
    """₹10 crore in paise is 10^10 — well past float53 exactness for sums of
    many such values. Python ints are exact; this proves nothing casts to float."""
    from app.engine import run_batch

    rt = Runtime.build(store_path=":memory:")
    leaks = rt.sources["simulator"].pull(seed=5, count=40).leaks
    huge = 9_99_99_99_900  # ₹9,99,99,999 in paise
    for ev in leaks:
        ev.amount_paise = huge
    b = run_batch(rt.uplift, rt.corpus, rt.memory, rt.executor, rt.diagnoser,
                  rt.merchant, leaks, source_name="simulator", seed=5)["batch"]
    assert sum(e["amountPaise"] for e in b["events"]) == huge * 40
    for key in ("A", "B"):
        m = b["agents"][key]["metrics"]
        assert isinstance(m["recoveredPaise"], int) and isinstance(m["netValuePaise"], int)
        assert m["netValuePaise"] == m["recoveredPaise"] - m["contactCostPaise"] - m["outreachCausedChurnLossPaise"]


def test_message_cost_tracks_the_class_the_gate_assigned():
    """A promotional WhatsApp costs ~7x a service one. Charging the cheap rate
    for an expensive message understates spend on exactly the messages that
    need the most scrutiny."""
    m = MerchantConfig()
    assert m.cost_for("payment_link_whatsapp", "promotional") > m.cost_for("payment_link_whatsapp", "service")
    assert m.cost_for("silent_retry", None) == 0
    assert m.cost_for("no_action", None) == 0
    assert m.cost_for("voice_call", "service") > m.cost_for("payment_link_sms", "service")


# ================================================================== privacy


CONTACT = "+919812345678"
EMAIL = "buyer.private@example.com"

# A phone number and a large paise amount are the same digits — ₹9,99,99,999
# is 9999999900, which is also a valid Indian mobile. They are only
# distinguishable by type: an identifier arrives as a JSON *string*, money as a
# *number*. So the scanner walks the structure and inspects string leaves only,
# rather than regexing a flattened blob and flagging the ledger's own amounts.
# The guards are alphanumeric, not just numeric: a SHA-256 hash is hex, so it
# contains ten-digit runs at random — which made this scanner fail on roughly
# one run in three until the surrounding characters were excluded too.
PHONE_RE = re.compile(r"(?<![0-9A-Za-z])(?:\+?91[\-\s]?)?[6-9]\d{9}(?![0-9A-Za-z])")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def scan_for_identifiers(payload) -> list[str]:
    """Every phone- or email-shaped *string* in a structure, with its path."""
    found: list[str] = []
    for path, value in _walk(payload):
        if not isinstance(value, str):
            continue
        for m in itertools.chain(PHONE_RE.finditer(value), EMAIL_RE.finditer(value)):
            found.append(f"{path} = {m.group(0)!r}")
    return found


@pytest.fixture(scope="module")
def real_run(tmp_path_factory):
    """A real-data batch carrying deliberately identifiable contact details."""
    import time

    r = Runtime.build(store_path=tmp_path_factory.mktemp("pii") / "ledger.db")
    now = int(time.time())
    rows = [{
        "id": f"pay_pii_{i}", "amount": 129900, "status": "failed", "method": "card",
        "card": {"network": "Visa", "issuer": "HDFC"}, "token_id": "tok",
        "customer_id": f"cust_pii_{i}", "contact": CONTACT, "email": EMAIL,
        "error_reason": "insufficient_funds", "error_source": "customer",
        "created_at": now - 600 - i * 60,
    } for i in range(12)]
    meta = r.sources["file"].save(json.dumps({"items": rows}).encode(), "pii.json")
    summary = r.run_and_store("file", file_id=meta["fileId"])
    return r, summary["batchId"]


def test_raw_contact_details_never_enter_the_stored_batch_or_traces(real_run):
    """DPDP: the ledger is a durable record read by operators. It must carry a
    hash, not the customer's phone number."""
    rt, batch_id = real_run
    batch = rt.store.get_batch(batch_id)
    payload = [batch] + [rt.store.get_trace(e["eventId"], batch_id) for e in batch["events"]]
    blob = json.dumps(payload, ensure_ascii=False)
    assert CONTACT not in blob and EMAIL not in blob
    found = scan_for_identifiers(payload)
    assert not found, f"identifiers reached the batch: {found[:5]}"


def test_the_audit_log_carries_no_identifiers(real_run):
    rt, _ = real_run
    rows = rt.store.audit_tail(limit=5000)
    blob = json.dumps(rows, ensure_ascii=False)
    assert CONTACT not in blob and EMAIL not in blob
    found = scan_for_identifiers(rows)
    assert not found, f"identifiers reached the audit log: {found[:5]}"


def test_the_leaks_table_stores_a_hash_and_not_the_number(real_run):
    rt, batch_id = real_run
    rows = rt.store.leaks_for_batch(batch_id)
    assert rows
    for r in rows:
        assert r["contact_hash"] and len(r["contact_hash"]) == 16
        assert CONTACT not in json.dumps(dict(r), default=str)


def test_the_contact_hash_is_stable_but_not_reversible():
    a = LeakEvent(event_id="e1", contact=CONTACT)
    b = LeakEvent(event_id="e2", contact=CONTACT)
    c = LeakEvent(event_id="e3", contact="+919000000000")
    assert a.contact_hash() == b.contact_hash(), "the same number must group"
    assert a.contact_hash() != c.contact_hash()
    assert CONTACT not in a.contact_hash()
    # A truncated SHA-256, not an encoding of the input.
    assert a.contact_hash() == hashlib.sha256(CONTACT.encode()).hexdigest()[:16]
    assert LeakEvent(event_id="e4", contact=None).contact_hash() is None


def test_the_executor_still_gets_the_real_contact_it_needs(real_run):
    """Redaction must not break the product: Razorpay is the registered sender
    and needs the actual number to deliver a payment link."""
    rt, batch_id = real_run
    leaks = rt.sources["file"].pull(file_id=rt.sources["file"].list_files()[0]["fileId"]).leaks
    assert any(ev.contact == CONTACT for ev in leaks), "the pipeline lost the contact entirely"


# ================================================================ integrity


def _tamper(store: Store, sql: str, params=()):
    """Write directly to the database, behind the store's own API."""
    with store.transaction() as c:
        c.execute(sql, params)


def test_the_audit_chain_detects_an_edited_payload(tmp_path):
    s = Store(tmp_path / "t1.db")
    s.append_audit_many([("decision", {"i": i, "action": "silent_retry"}, "agent:B", f"evt_{i}") for i in range(30)])
    assert s.verify_audit()["ok"] is True
    _tamper(s, "UPDATE audit_log SET payload_json = ? WHERE seq = 14", ('{"i":14,"action":"payment_link_sms"}',))
    v = s.verify_audit()
    assert v["ok"] is False and v["firstBreak"] == 14
    s.close()


def test_the_audit_chain_detects_a_deleted_row(tmp_path):
    s = Store(tmp_path / "t2.db")
    s.append_audit_many([("decision", {"i": i}, "agent:B", None) for i in range(30)])
    _tamper(s, "DELETE FROM audit_log WHERE seq = 10")
    v = s.verify_audit()
    assert v["ok"] is False and v["firstBreak"] == 11, "removing a row must break the successor's link"
    s.close()


def test_the_audit_chain_detects_an_inserted_row(tmp_path):
    s = Store(tmp_path / "t3.db")
    s.append_audit_many([("decision", {"i": i}, "agent:B", None) for i in range(20)])
    row = s.conn.execute("SELECT * FROM audit_log WHERE seq = 5").fetchone()
    _tamper(
        s,
        "INSERT INTO audit_log (seq, at, actor, kind, ref, payload_json, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?)",
        (999, row["at"], "attacker", "decision", None, '{"i":"forged"}', row["prev_hash"], row["hash"]),
    )
    assert s.verify_audit()["ok"] is False
    s.close()


def test_the_audit_chain_detects_a_changed_actor_or_timestamp(tmp_path):
    """The hash covers the whole record, not just the payload — so back-dating
    an entry or re-attributing it to another actor is also detected."""
    s = Store(tmp_path / "t4.db")
    s.append_audit_many([("decision", {"i": i}, "agent:B", None) for i in range(10)])
    _tamper(s, "UPDATE audit_log SET actor = 'someone_else' WHERE seq = 4")
    assert s.verify_audit()["firstBreak"] == 4
    s2 = Store(tmp_path / "t5.db")
    s2.append_audit_many([("decision", {"i": i}, "agent:B", None) for i in range(10)])
    _tamper(s2, "UPDATE audit_log SET at = '2000-01-01T00:00:00.000+00:00' WHERE seq = 6")
    assert s2.verify_audit()["firstBreak"] == 6
    s.close(); s2.close()


def test_a_forged_chain_cannot_be_rebuilt_without_detection(tmp_path):
    """An attacker who edits a row and recomputes only that row's hash still
    breaks the next link, because every subsequent hash depends on it."""
    s = Store(tmp_path / "t6.db")
    s.append_audit_many([("decision", {"i": i}, "agent:B", None) for i in range(15)])
    row = s.conn.execute("SELECT * FROM audit_log WHERE seq = 7").fetchone()
    forged_payload = '{"i":"forged"}'
    body = json.dumps(
        {"kind": row["kind"], "actor": row["actor"], "ref": row["ref"], "at": row["at"],
         "payload": json.loads(forged_payload)},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    forged_hash = hashlib.sha256(f"{row['prev_hash']}\n{body}".encode()).hexdigest()
    _tamper(s, "UPDATE audit_log SET payload_json = ?, hash = ? WHERE seq = 7", (forged_payload, forged_hash))
    v = s.verify_audit()
    assert v["ok"] is False and v["firstBreak"] == 8, "the successor link must expose the forgery"
    s.close()


def test_an_untouched_chain_of_a_real_run_verifies(rt):
    v = rt.store.verify_audit()
    assert v["ok"] is True and v["firstBreak"] is None and v["rows"] > 100


def test_stored_traces_are_immutable_under_outcome_attribution(real_run):
    """Attribution overlays an outcome at read time; the stored decision must
    stay exactly as it was written, or the audit trail is retrospective."""
    rt, batch_id = real_run
    pending = rt.store.pending_real_leaks()
    leak = pending[0]
    before = copy.deepcopy(rt.store.get_trace(leak["event_id"], batch_id))
    rt.outcomes.mark(leak["event_id"], recovered=True, note="reconciled by hand")
    after = rt.store.get_trace(leak["event_id"], batch_id)
    assert after == before, "the stored trace was rewritten"
    overlay = rt.trace_with_outcome(leak["event_id"], batch_id)
    assert overlay["agentB"]["outcome"] == {"recovered": True, "churned": False}


def test_the_identifier_scanner_actually_detects_identifiers():
    """A privacy test that cannot fail is worthless. Prove the detector fires on
    real identifiers and stays quiet on everything that merely looks like one."""
    assert scan_for_identifiers({"contact": CONTACT})
    assert scan_for_identifiers({"email": EMAIL})
    assert scan_for_identifiers({"a": {"b": [{"phone": "9812345678"}]}}), "must search nested structures"

    # Not identifiers: an amount that happens to be ten digits, a batch id's
    # timestamp, a hash, and a customer id built from one.
    assert scan_for_identifiers({"amountPaise": 9999999900}) == []
    assert scan_for_identifiers({"batchId": "bat_live_20260905162456_e49b"}) == []
    assert scan_for_identifiers({"hash": "d02ed208baea7f1cbe6acfd220cd6ce4"}) == []
    assert scan_for_identifiers({"contactHash": "a1b2c3d4e5f60718"}) == []
    # A hex hash that happens to contain a ten-digit run starting 6-9. This is
    # the exact shape that made the suite flaky before the alphanumeric guards.
    assert scan_for_identifiers({"hash": "d02ed208baea7f1cbe6589595338aa01"}) == []
    assert scan_for_identifiers({"prevHash": "0" * 24 + "6589595338" + "ff"}) == []
