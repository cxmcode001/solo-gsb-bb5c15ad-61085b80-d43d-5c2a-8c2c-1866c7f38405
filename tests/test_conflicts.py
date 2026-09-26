"""Unit tests for conflict-group assembly (build_conflict_groups)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.conflicts import build_conflict_groups
from app.separation import (
    CapacityViolation,
    ClosureConflict,
    Flight,
    Violation,
    sort_flights,
)

UTC = timezone.utc
BASE = datetime(2026, 9, 18, tzinfo=UTC)


def dt(seconds: int) -> datetime:
    return BASE + timedelta(seconds=seconds)


def _flight(no: str, t: int, wake: str = "M") -> Flight:
    return Flight(flight_no=no, scheduled_off=dt(t), wake=wake, runway="36L")


def _wake(leader: str, follower: str) -> Violation:
    return Violation(
        leader_no=leader,
        follower_no=follower,
        leader_wake="H",
        follower_wake="H",
        leader_time=dt(0),
        follower_time=dt(30),
        required_seconds=100,
        actual_seconds=30,
        shortfall_seconds=70,
        leader_position=1,
        follower_position=2,
    )


def _closure(no: str, t: int) -> ClosureConflict:
    return ClosureConflict(
        flight_no=no,
        wake="M",
        scheduled_off=dt(t),
        position=1,
        window_index=1,
        window_start=dt(t - 30),
        window_end=dt(t + 30),
    )


def _capacity(anchor: str, anchor_t: int, window_seconds: int = 90) -> CapacityViolation:
    return CapacityViolation(
        flight_no="XX",
        wake="M",
        scheduled_off=dt(anchor_t + 1),
        position=3,
        window_seconds=window_seconds,
        max_departures=2,
        anchor_flight_no=anchor,
        anchor_position=1,
        anchor_off=dt(anchor_t),
        window_end=dt(anchor_t + window_seconds),
        departures_in_window=3,
        earliest_compliant_off=dt(anchor_t + window_seconds),
    )


def test_no_conflicts_no_groups():
    flights = [_flight("CA1", 0), _flight("CA2", 300)]
    assert build_conflict_groups(flights, [], [], []) == []


def test_wake_violation_forms_single_group():
    flights = [_flight("CA1", 0), _flight("CA2", 30), _flight("CA3", 600)]
    groups = build_conflict_groups(flights, [_wake("CA1", "CA2")], [], [])
    assert len(groups) == 1
    group = groups[0]
    assert [f.flight_no for f in group.flights] == ["CA1", "CA2"]
    assert [f.position for f in group.flights] == [1, 2]
    assert (group.wake_count, group.capacity_count, group.closure_count) == (1, 0, 0)


def test_closure_conflict_is_singleton():
    flights = [_flight("CA1", 0), _flight("CA2", 300)]
    groups = build_conflict_groups(flights, [], [_closure("CA2", 300)], [])
    assert len(groups) == 1
    assert [f.flight_no for f in groups[0].flights] == ["CA2"]
    assert groups[0].closure_count == 1


def test_capacity_window_links_every_flight_inside_window():
    flights = [
        _flight("CA1", 0),
        _flight("CA2", 40),
        _flight("CA3", 80),
        _flight("CA4", 200),
    ]
    # window [0, 90) holds CA1..CA3; CA4 lies outside
    groups = build_conflict_groups(flights, [], [], [_capacity("CA1", 0)])
    assert len(groups) == 1
    assert [f.flight_no for f in groups[0].flights] == ["CA1", "CA2", "CA3"]
    assert groups[0].capacity_count == 1


def test_overlapping_capacity_windows_merge():
    flights = [
        _flight("CA1", 0),
        _flight("CA2", 40),
        _flight("CA3", 80),
        _flight("CA4", 120),
    ]
    # [0, 90) holds CA1..CA3, [40, 130) holds CA2..CA4 -> one component
    groups = build_conflict_groups(
        flights, [], [], [_capacity("CA1", 0), _capacity("CA2", 40)]
    )
    assert len(groups) == 1
    assert [f.flight_no for f in groups[0].flights] == ["CA1", "CA2", "CA3", "CA4"]
    assert groups[0].capacity_count == 2


def test_wake_edge_merges_closure_singleton():
    flights = [_flight("CA1", 0), _flight("CA2", 30)]
    groups = build_conflict_groups(
        flights, [_wake("CA1", "CA2")], [_closure("CA2", 30)], []
    )
    assert len(groups) == 1
    assert groups[0].wake_count == 1
    assert groups[0].closure_count == 1


def test_unrelated_adjacent_conflicts_do_not_merge_and_groups_sort_by_earliest():
    flights = [_flight("CA1", 60), _flight("CA2", 600), _flight("CA3", 630)]
    groups = build_conflict_groups(
        flights, [_wake("CA2", "CA3")], [_closure("CA1", 60)], []
    )
    assert len(groups) == 2
    # the closure singleton is earlier, so it leads even though the wake edge
    # was processed first
    assert [f.flight_no for f in groups[0].flights] == ["CA1"]
    assert groups[0].closure_count == 1
    assert [f.flight_no for f in groups[1].flights] == ["CA2", "CA3"]
    assert groups[1].wake_count == 1


def test_group_flights_sorted_by_time_then_flight_no():
    flights = sort_flights([_flight("BB", 0), _flight("AA", 0)])
    groups = build_conflict_groups(flights, [_wake("AA", "BB")], [], [])
    assert [f.flight_no for f in groups[0].flights] == ["AA", "BB"]
    assert [f.position for f in groups[0].flights] == [1, 2]
