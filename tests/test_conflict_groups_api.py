"""End-to-end tests for GET /api/v1/batches/{batch_id}/conflict-groups."""

from __future__ import annotations


def _flight(no: str, off: str, wake: str) -> dict:
    return {"flight_no": no, "scheduled_off": off, "wake": wake}


def _validate(client, body: dict) -> str:
    r = client.post("/api/v1/validate", json=body)
    assert r.status_code == 200, r.text
    return r.json()["batch_id"]


def _groups(client, batch_id: str) -> dict:
    r = client.get(f"/api/v1/batches/{batch_id}/conflict-groups")
    assert r.status_code == 200, r.text
    return r.json()


def test_conflict_groups_unknown_batch_returns_404(client):
    r = client.get("/api/v1/batches/does-not-exist/conflict-groups")
    assert r.status_code == 404


def test_conflict_groups_rejects_non_validate_modes(client):
    body = {
        "runways": [
            {
                "runway": "36L",
                "flights": [
                    _flight("CA1", "2026-09-18T00:00:00Z", "J"),
                    _flight("CA2", "2026-09-18T00:00:50Z", "M"),
                ],
            }
        ]
    }
    for path in (
        "/api/v1/trial",
        "/api/v1/trial/best-adjacent-swap",
        "/api/v1/trial/joint",
    ):
        batch_id = client.post(path, json=body).json()["batch_id"]
        r = client.get(f"/api/v1/batches/{batch_id}/conflict-groups")
        assert r.status_code == 422, path
        assert "validate" in r.text


def test_wake_pair_closure_singleton_and_uninvolved_excluded(client):
    body = {
        "runways": [
            {
                "runway": "36L",
                "flights": [
                    _flight("CA1", "2026-09-18T00:00:00Z", "J"),
                    _flight("CA2", "2026-09-18T00:00:30Z", "M"),  # 30s < J->M 180s
                    _flight("CA3", "2026-09-18T00:05:00Z", "M"),  # inside closure
                    _flight("CA4", "2026-09-18T00:10:00Z", "M"),  # no conflict
                ],
                "closures": [
                    {"start": "2026-09-18T00:04:30Z", "end": "2026-09-18T00:05:30Z"}
                ],
            }
        ]
    }
    batch_id = _validate(client, body)
    data = _groups(client, batch_id)
    assert data["batch_id"] == batch_id
    assert data["mode"] == "validate"
    assert data["total_runways"] == 1
    assert data["total_groups"] == 2
    assert data["total_flights_in_groups"] == 3

    rw = data["runways"][0]
    assert rw["runway"] == "36L"
    assert rw["group_count"] == 2
    first, second = rw["groups"]
    # wake group leads: its earliest flight departs first
    assert [f["flight_no"] for f in first["flights"]] == ["CA1", "CA2"]
    assert first["wake_count"] == 1
    assert first["capacity_count"] == 0
    assert first["closure_count"] == 0
    assert first["flights"][0] == {
        "position": 1,
        "flight_no": "CA1",
        "scheduled_off": "2026-09-18T00:00:00Z",
        "wake": "J",
    }
    # closure conflict is a singleton group
    assert [f["flight_no"] for f in second["flights"]] == ["CA3"]
    assert second["wake_count"] == 0
    assert second["capacity_count"] == 0
    assert second["closure_count"] == 1
    # CA4 takes part in no conflict and is not output anywhere
    assert all(
        f["flight_no"] != "CA4"
        for group in rw["groups"]
        for f in group["flights"]
    )


def test_overlapping_capacity_windows_merge_into_one_group(client):
    body = {
        "runways": [
            {
                "runway": "36L",
                "flights": [
                    _flight("CA1", "2026-09-18T00:00:00Z", "M"),
                    _flight("CA2", "2026-09-18T00:00:40Z", "M"),
                    _flight("CA3", "2026-09-18T00:01:20Z", "M"),
                    _flight("CA4", "2026-09-18T00:02:00Z", "M"),
                ],
                "capacity": {"window_seconds": 90, "max_departures": 2},
            }
        ],
        "matrix": [{"leader": "M", "follower": "M", "seconds": 0}],
    }
    batch_id = _validate(client, body)
    data = _groups(client, batch_id)
    rw = data["runways"][0]
    # CA3 overflows the window anchored at CA1 ([00:00, 01:30) holds CA1..CA3),
    # CA4 overflows the one anchored at CA2 ([00:00:40, 01:10)... holds CA2..CA4);
    # the windows share CA2/CA3 and merge into a single group
    assert rw["group_count"] == 1
    group = rw["groups"][0]
    assert [f["flight_no"] for f in group["flights"]] == ["CA1", "CA2", "CA3", "CA4"]
    assert [f["position"] for f in group["flights"]] == [1, 2, 3, 4]
    assert group["wake_count"] == 0
    assert group["capacity_count"] == 2
    assert group["closure_count"] == 0
    assert data["total_groups"] == 1
    assert data["total_flights_in_groups"] == 4


def test_closure_flight_merges_into_wake_group(client):
    body = {
        "runways": [
            {
                "runway": "36L",
                "flights": [
                    _flight("CA1", "2026-09-18T00:00:00Z", "J"),
                    _flight("CA2", "2026-09-18T00:00:30Z", "M"),  # wake + closure
                ],
                "closures": [
                    {"start": "2026-09-18T00:00:15Z", "end": "2026-09-18T00:00:45Z"}
                ],
            }
        ]
    }
    batch_id = _validate(client, body)
    rw = _groups(client, batch_id)["runways"][0]
    assert rw["group_count"] == 1
    group = rw["groups"][0]
    assert [f["flight_no"] for f in group["flights"]] == ["CA1", "CA2"]
    assert group["wake_count"] == 1
    assert group["closure_count"] == 1


def test_unrelated_conflicts_stay_separate_and_groups_sorted(client):
    body = {
        "runways": [
            {
                "runway": "36L",
                "flights": [
                    _flight("CA1", "2026-09-18T00:01:00Z", "M"),  # closure singleton
                    _flight("CA2", "2026-09-18T00:10:00Z", "H"),
                    _flight("CA3", "2026-09-18T00:10:30Z", "H"),  # 30s < H->H 100s
                ],
                "closures": [
                    {"start": "2026-09-18T00:00:30Z", "end": "2026-09-18T00:01:30Z"}
                ],
            }
        ]
    }
    batch_id = _validate(client, body)
    rw = _groups(client, batch_id)["runways"][0]
    # CA1 sits next to CA2 in the plan but shares no conflict with it
    assert rw["group_count"] == 2
    first, second = rw["groups"]
    # groups order by earliest flight: the 00:01 singleton leads
    assert [f["flight_no"] for f in first["flights"]] == ["CA1"]
    assert first["closure_count"] == 1
    assert [f["flight_no"] for f in second["flights"]] == ["CA2", "CA3"]
    assert second["wake_count"] == 1


def test_clean_batch_yields_empty_groups(client):
    body = {
        "runways": [
            {
                "runway": "36L",
                "flights": [
                    _flight("CA1", "2026-09-18T00:00:00Z", "M"),
                    _flight("CA2", "2026-09-18T00:05:00Z", "M"),
                ],
            }
        ]
    }
    batch_id = _validate(client, body)
    data = _groups(client, batch_id)
    assert data["total_groups"] == 0
    assert data["total_flights_in_groups"] == 0
    rw = data["runways"][0]
    assert rw["runway"] == "36L"
    assert rw["group_count"] == 0
    assert rw["groups"] == []


def test_groups_are_per_runway_and_flights_sorted(client):
    body = {
        "runways": [
            {
                "runway": "36L",
                "flights": [
                    _flight("CA1", "2026-09-18T00:00:00Z", "J"),
                    _flight("CA2", "2026-09-18T00:00:30Z", "M"),  # 30s < J->M 180s
                ],
            },
            {
                "runway": "18R",
                "flights": [
                    _flight("BB", "2026-09-18T00:00:00Z", "H"),
                    _flight("AA", "2026-09-18T00:00:00Z", "H"),  # 0s < H->H 100s
                ],
            },
        ]
    }
    batch_id = _validate(client, body)
    data = _groups(client, batch_id)
    assert data["total_runways"] == 2
    assert data["total_groups"] == 2
    rw36l = next(rw for rw in data["runways"] if rw["runway"] == "36L")
    rw18r = next(rw for rw in data["runways"] if rw["runway"] == "18R")
    assert [f["flight_no"] for f in rw36l["groups"][0]["flights"]] == ["CA1", "CA2"]
    # equal times tie-break on flight number
    assert [f["flight_no"] for f in rw18r["groups"][0]["flights"]] == ["AA", "BB"]
    assert [f["position"] for f in rw18r["groups"][0]["flights"]] == [1, 2]
