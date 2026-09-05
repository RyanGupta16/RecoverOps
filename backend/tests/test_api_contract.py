"""Every HTTP endpoint, on the happy path and on the ways a caller gets it wrong.

The API is the only surface a merchant integrates against, so its contract is
the part that must not drift: shapes the frontend is typed against, status
codes a client branches on, and validation that refuses bad input rather than
half-processing it.

The app is built once against a throwaway ledger so these run hermetically —
no keys, no network, no shared state with a developer's own data.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import app.main as main
    from app.runtime import Runtime

    main.rt = Runtime.build(store_path=tmp_path_factory.mktemp("api") / "ledger.db")
    main.rt.run_and_store("simulator", seed=7, count=60)
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(scope="module")
def batch_id(client):
    return client.get("/api/batches").json()[0]["batchId"]


# ------------------------------------------------------------------ health


def test_health_reports_every_layer(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    for key in ("estimator", "benchmark", "retrieval", "razorpayLive", "llmLive", "merchant", "store", "learning"):
        assert key in d, key
    # Without credentials the API must say so rather than implying live integrations.
    assert d["razorpayLive"] is False and d["llmLive"] is False
    assert d["store"]["schemaVersion"] >= 4
    assert set(d["store"]["leaks"]) >= {"real", "pending", "resolved", "control", "explored", "synthetic"}


def test_merchant_config_is_served_without_secrets(client):
    d = client.get("/api/merchant").json()
    assert d["contactBudgetPerBatch"] > 0
    assert 0 <= d["holdoutShare"] <= 1
    assert d["windows"]["promotional"]["start"] < d["windows"]["promotional"]["end"]
    blob = json.dumps(d).lower()
    for leak in ("key_id", "key_secret", "api_key", "secret", "rzp_test", "rzp_live", "sk-ant"):
        assert leak not in blob, f"merchant config leaked {leak}"


def test_policy_rules_are_ordered_and_cited(client):
    rules = client.get("/api/policy/rules").json()
    assert len(rules) >= 25
    assert all({"id", "category", "description", "citation", "basis"} <= set(r) for r in rules)
    assert len({r["id"] for r in rules}) == len(rules), "rule ids must be unique"
    assert sum(1 for r in rules if r["citation"]) >= 15
    assert rules[0]["id"] == "NO_RETRY_ON_FRAUD", "fraud must be evaluated first"
    assert rules[-1]["id"] == "ESCALATE_UNRESOLVED", "escalation is the last resort"


def test_sources_advertise_availability_honestly(client):
    srcs = {s["name"]: s for s in client.get("/api/sources").json()}
    assert {"simulator", "razorpay", "file", "receivables", "checkout"} <= set(srcs)
    assert srcs["simulator"]["available"] is True
    # No keys in the suite, so the account pull must not claim to be available.
    assert srcs["razorpay"]["available"] is False
    assert srcs["simulator"]["dataMode"] == "synthetic"


# ------------------------------------------------------------------ batches


def test_batch_run_and_fetch_round_trip(client):
    r = client.post("/api/batch/run", json={"source": "simulator", "seed": 11, "count": 40})
    assert r.status_code == 200
    body = r.json()
    assert body["eventCount"] == 40 and body["dataMode"] == "synthetic"

    got = client.get(f"/api/batch/{body['batchId']}/results")
    assert got.status_code == 200
    batch = got.json()
    assert batch["batchId"] == body["batchId"]
    assert len(batch["events"]) == 40
    for key in ("agents", "honesty", "assumptions", "pipelineStats", "streamScript", "sleepingDogs", "exceptions"):
        assert key in batch, key
    assert set(batch["agents"]) == {"A", "B"}


@pytest.mark.parametrize(
    "payload,status",
    [
        ({"source": "nonsense"}, 422),          # not in the enum
        ({"source": "simulator", "count": 1}, 422),      # below the floor
        ({"source": "simulator", "count": 999999}, 422), # above the ceiling
        ({"source": "simulator", "days": 0}, 422),
        ({"source": "file"}, 422),               # file source with no fileId
        ({"source": "file", "fileId": "file_missing"}, 422),
    ],
)
def test_bad_run_requests_are_refused_not_half_processed(client, payload, status):
    before = len(client.get("/api/batches?limit=200").json())
    r = client.post("/api/batch/run", json=payload)
    assert r.status_code == status, r.text
    assert "detail" in r.json()
    after = len(client.get("/api/batches?limit=200").json())
    assert after == before, "a refused run must not leave a batch behind"


def test_unknown_ids_are_404_not_500(client):
    assert client.get("/api/batch/bat_nope/results").status_code == 404
    assert client.get("/api/batch/bat_nope/sleeping-dogs").status_code == 404
    assert client.get("/api/batch/bat_nope/exceptions").status_code == 404
    assert client.get("/api/events/evt_nope/trace").status_code == 404
    assert client.get("/api/batch/stream", params={"batch_id": "bat_nope"}).status_code == 404


def test_batches_listing_respects_limits(client):
    assert client.get("/api/batches?limit=1").status_code == 200
    assert len(client.get("/api/batches?limit=1").json()) == 1
    assert client.get("/api/batches?limit=0").status_code == 422
    assert client.get("/api/batches?limit=9999").status_code == 422


def test_latest_batch_matches_the_history_head(client):
    latest = client.get("/api/batch/latest").json()
    assert latest["batchId"] == client.get("/api/batches?limit=1").json()[0]["batchId"]


def test_sse_stream_is_well_formed(client, batch_id):
    with client.stream("GET", "/api/batch/stream", params={"batch_id": batch_id}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers.get("cache-control") == "no-cache"
        body = "".join(r.iter_text())
    data_lines = [ln for ln in body.splitlines() if ln.startswith("data: ")]
    assert len(data_lines) > 10
    first = json.loads(data_lines[0][6:])
    assert {"kind", "text"} <= set(first)
    assert body.rstrip().endswith("data: {}"), "the stream must close with a done event"


def test_trace_carries_the_whole_decision_chain(client, batch_id):
    event_id = client.get(f"/api/batch/{batch_id}/results").json()["events"][0]["eventId"]
    t = client.get(f"/api/events/{event_id}/trace").json()
    for key in ("diagnosis", "precedents", "uplift", "agentA", "agentB", "leak", "dataMode"):
        assert key in t, key
    gate = t["agentB"]["gate"]
    assert len(gate) >= 25
    assert all({"ruleId", "verdict", "note"} <= set(g) for g in gate)
    assert {g["verdict"] for g in gate} <= {"PASS", "BLOCK", "N/A"}
    # At most one BLOCK: the first one stops evaluation.
    assert sum(1 for g in gate if g["verdict"] == "BLOCK") <= 1


# -------------------------------------------------------- outcomes, learning


def test_outcome_mark_validates_and_reports_clearly(client):
    assert client.post("/api/outcomes/mark", json={"eventId": "evt_nope", "recovered": True}).status_code == 404
    assert client.post("/api/outcomes/mark", json={"recovered": True}).status_code == 422  # no eventId
    assert client.post("/api/outcomes/mark", json={"eventId": "x", "recovered": "maybe"}).status_code == 422


def test_learning_status_is_honest_before_any_real_data(client):
    d = client.get("/api/learning/status").json()
    assert d["estimatorMode"] == "priors"
    assert d["policyEffect"]["measurable"] is False
    assert d["policyEffect"]["ateRate"] is None
    assert d["thresholds"]["minRows"] > 0


def test_sync_without_credentials_says_so(client):
    d = client.post("/api/outcomes/sync").json()
    assert d["live"] is False and d["recovered"] == 0


# ------------------------------------------------------------------ webhooks


def test_webhook_refuses_everything_without_a_secret(client):
    assert client.get("/api/webhooks/status").json()["configured"] is False
    r = client.post("/webhooks/razorpay", json={"event": "payment_link.paid"})
    assert r.status_code == 503, "an unsigned webhook must never be acted on"


# --------------------------------------------------------------------- misc


def test_promises_and_degradation_endpoints_are_shaped(client):
    p = client.get("/api/promises").json()
    assert "stats" in p and "promises" in p
    d = client.get("/api/degradation").json()
    assert {"cohorts", "live", "feedAvailable"} <= set(d)
    assert d["feedAvailable"] is False  # no client configured
    v = client.get("/api/voice/status").json()
    assert v["live"] is False and v["ttsModel"]


def test_ingest_rejects_unparseable_and_oversized_files(client):
    r = client.post("/api/ingest/file", files={"file": ("x.json", b"{not json", "application/json")})
    assert r.status_code == 400 and "detail" in r.json()
    big = b"a" * (26 * 1024 * 1024)
    assert client.post("/api/ingest/file", files={"file": ("big.csv", big, "text/csv")}).status_code == 413


def test_audit_verifies_and_filters(client):
    v = client.get("/api/audit/verify").json()
    assert v["ok"] is True and v["firstBreak"] is None and v["rows"] > 0
    tail = client.get("/api/audit?limit=5").json()
    assert len(tail["rows"]) == 5 and tail["total"] >= 5
    only = client.get("/api/audit?limit=50&kind=batch.completed").json()
    assert all(r["kind"] == "batch.completed" for r in only["rows"])
