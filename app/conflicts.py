"""Conflict-group assembly for stored validate batches.

Three conflict relations link flights that must be handled together:

* a wake-separation violation links the leader and the follower;
* a rolling-capacity overflow links every same-runway flight inside its
  triggering window ``[window_start, window_end)``;
* a closure-window conflict marks the hit flight as a singleton conflict.

A runway's conflict groups are the connected components of this graph:
overlapping capacity windows and wake edges merge through shared flights,
while conflicts that share no flight stay in separate groups no matter how
close their flights sit in the planned order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .separation import CapacityViolation, ClosureConflict, Flight, Violation
from .timeutils import to_epoch_seconds


@dataclass(frozen=True, slots=True)
class ConflictGroupFlight:
    """A flight taking part in a conflict group, with its plan position."""

    position: int  # 1-based stable position within the runway's plan
    flight_no: str
    scheduled_off: datetime  # always tz-aware, UTC
    wake: str


@dataclass(frozen=True, slots=True)
class ConflictGroup:
    """One connected conflict component: the flights to handle together and
    the number of wake / capacity / closure conflicts the component holds."""

    flights: tuple[ConflictGroupFlight, ...]  # sorted by (scheduled_off, flight_no)
    wake_count: int
    capacity_count: int
    closure_count: int

    @property
    def sort_key(self) -> tuple[int, str]:
        """Groups order by their earliest flight (time, then flight number)."""
        first = self.flights[0]
        return to_epoch_seconds(first.scheduled_off), first.flight_no


def build_conflict_groups(
    flights: list[Flight],
    violations: list[Violation],
    closure_conflicts: list[ClosureConflict],
    capacity_violations: list[CapacityViolation],
) -> list[ConflictGroup]:
    """Connected conflict components of one runway, ordered by earliest flight.

    ``flights`` is the runway's full plan in stable order; only flights that
    take part in at least one conflict appear in the result.
    """
    parent: dict[str, str] = {}

    def find(no: str) -> str:
        root = no
        while parent[root] != root:
            root = parent[root]
        while parent[no] != root:  # path compression
            parent[no], no = root, parent[no]
        return root

    def touch(no: str) -> None:
        parent.setdefault(no, no)

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    # Wake violations link the leader and the follower.
    for v in violations:
        touch(v.leader_no)
        touch(v.follower_no)
        union(v.leader_no, v.follower_no)

    # Closure conflicts mark the hit flight as a (possibly singleton) node.
    for c in closure_conflicts:
        touch(c.flight_no)

    # Capacity overflows link every flight inside the triggering window.
    window_members: list[tuple[str, ...]] = []
    for cv in capacity_violations:
        start_t = to_epoch_seconds(cv.anchor_off)
        end_t = to_epoch_seconds(cv.window_end)
        members = tuple(f.flight_no for f in flights if start_t <= f.t < end_t)
        if not members:  # unreachable: the anchor lies inside its own window
            members = (cv.anchor_flight_no,)
        for no in members:
            touch(no)
        for no in members[1:]:
            union(members[0], no)
        window_members.append(members)

    position_of = {f.flight_no: pos for pos, f in enumerate(flights, start=1)}
    by_no = {f.flight_no: f for f in flights}
    components: dict[str, list[str]] = {}
    for no in parent:
        components.setdefault(find(no), []).append(no)

    groups: list[ConflictGroup] = []
    for numbers in components.values():
        member_set = set(numbers)
        ordered = sorted(numbers, key=lambda no: (by_no[no].t, by_no[no].flight_no))
        groups.append(
            ConflictGroup(
                flights=tuple(
                    ConflictGroupFlight(
                        position=position_of[no],
                        flight_no=no,
                        scheduled_off=by_no[no].scheduled_off,
                        wake=by_no[no].wake,
                    )
                    for no in ordered
                ),
                wake_count=sum(1 for v in violations if v.leader_no in member_set),
                capacity_count=sum(
                    1 for members in window_members if members[0] in member_set
                ),
                closure_count=sum(
                    1 for c in closure_conflicts if c.flight_no in member_set
                ),
            )
        )
    groups.sort(key=lambda g: g.sort_key)
    return groups
