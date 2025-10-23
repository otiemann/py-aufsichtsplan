from __future__ import annotations

from datetime import date
from typing import Dict

from app.services.cp_sat_solver import (
    BreakSlotSpec,
    BreakSupervisionSolver,
    TeacherSpec,
)


def _make_teacher(
    teacher_id: int,
    target: int,
    periods_by_day: Dict[int, set[int]],
    *,
    preferred_floor: int | None = None,
    prio_rank: int = 10,
) -> TeacherSpec:
    return TeacherSpec(
        id=teacher_id,
        target=target,
        prio_rank=prio_rank,
        preferred_floor=preferred_floor,
        floor_weights=None,
        day_periods={day: frozenset(periods) for day, periods in periods_by_day.items()},
    )


def _make_slot(
    slot_id: str,
    dt: date,
    break_index: int,
    *,
    before_period: int | None,
    after_period: int | None,
    needs: Dict[int, int],
) -> BreakSlotSpec:
    return BreakSlotSpec(
        slot_id=slot_id,
        date=dt,
        day_index=dt.weekday(),
        break_index=break_index,
        before_period=before_period,
        after_period=after_period,
        needs=needs,
    )


def test_solver_respects_adjacency() -> None:
    teachers = [
        _make_teacher(1, target=1, periods_by_day={}),  # keine Stunden
        _make_teacher(2, target=1, periods_by_day={0: {2}}),  # Unterricht vor der Pause
    ]
    slot = _make_slot(
        "slot-1",
        dt=date(2024, 9, 2),
        break_index=2,
        before_period=2,
        after_period=3,
        needs={1: 1},
    )

    solver = BreakSupervisionSolver(
        teachers=teachers,
        break_slots=[slot],
        floor_ids=[1],
        fairness_band=None,
        max_one_per_day=False,
        time_limit_s=5.0,
    )
    result = solver.solve()

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert {(assignment.teacher_id, assignment.floor_id) for assignment in result.assignments} == {(2, 1)}
    assert result.loads[1] == 0
    assert result.loads[2] == 1
    assert result.daily_excess == 0
    assert result.total_shortfall == 0
    assert result.band_violation == 0


def test_solver_respects_fairness_band() -> None:
    teachers = [
        _make_teacher(1, target=1, periods_by_day={0: {2}, 1: {2}}),
        _make_teacher(2, target=1, periods_by_day={0: {2}, 1: {2}}),
    ]
    slots = [
        _make_slot(
            "slot-1",
            dt=date(2024, 9, 2),
            break_index=2,
            before_period=2,
            after_period=3,
            needs={1: 1},
        ),
        _make_slot(
            "slot-2",
            dt=date(2024, 9, 3),
            break_index=2,
            before_period=2,
            after_period=3,
            needs={1: 1},
        ),
    ]

    solver = BreakSupervisionSolver(
        teachers=teachers,
        break_slots=slots,
        floor_ids=[1],
        fairness_band=0,
        max_one_per_day=False,
        time_limit_s=5.0,
    )
    result = solver.solve()

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert result.max_dev == 0
    assert result.loads[1] == 1
    assert result.loads[2] == 1
    assert result.daily_excess == 0
    assert result.band_violation == 0
    assert result.total_shortfall == 0


def test_floor_preference_is_respected() -> None:
    teachers = [
        _make_teacher(1, target=1, periods_by_day={0: {2}}, preferred_floor=1),
        _make_teacher(2, target=1, periods_by_day={0: {2}}, preferred_floor=2),
    ]
    slot = _make_slot(
        "slot-1",
        dt=date(2024, 9, 2),
        break_index=2,
        before_period=2,
        after_period=3,
        needs={1: 1, 2: 1},
    )

    solver = BreakSupervisionSolver(
        teachers=teachers,
        break_slots=[slot],
        floor_ids=[1, 2],
        fairness_band=0,
        max_one_per_day=False,
        time_limit_s=5.0,
    )
    result = solver.solve()

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assigned_pairs = {(assignment.teacher_id, assignment.floor_id) for assignment in result.assignments}
    assert assigned_pairs == {(1, 1), (2, 2)}
    assert result.priority_cost == 0
    assert result.daily_excess == 0
    assert result.total_shortfall == 0


def test_daily_penalty_counts_excess_assignments() -> None:
    teachers = [
        _make_teacher(1, target=2, periods_by_day={0: {2}}),
        _make_teacher(2, target=0, periods_by_day={}),
    ]
    slots = [
        _make_slot(
            "slot-1",
            dt=date(2024, 9, 2),
            break_index=2,
            before_period=2,
            after_period=3,
            needs={1: 1},
        ),
        _make_slot(
            "slot-2",
            dt=date(2024, 9, 2),
            break_index=3,
            before_period=4,
            after_period=5,
            needs={1: 1},
        ),
    ]

    solver = BreakSupervisionSolver(
        teachers=teachers,
        break_slots=slots,
        floor_ids=[1],
        fairness_band=None,
        max_one_per_day=False,
        time_limit_s=5.0,
    )
    result = solver.solve()

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert result.loads[1] == 2
    assert result.daily_excess == 1
    assert result.total_shortfall == 0


def test_respects_max_extra_duties_cap() -> None:
    teachers = [
        _make_teacher(1, target=1, periods_by_day={0: {2}}, preferred_floor=1),
        _make_teacher(2, target=1, periods_by_day={0: {2}}),
    ]
    slots = [
        _make_slot(
            "slot-1",
            dt=date(2024, 9, 2),
            break_index=2,
            before_period=2,
            after_period=3,
            needs={1: 1},
        ),
        _make_slot(
            "slot-2",
            dt=date(2024, 9, 3),
            break_index=2,
            before_period=2,
            after_period=3,
            needs={1: 1},
        ),
    ]

    solver = BreakSupervisionSolver(
        teachers=teachers,
        break_slots=slots,
        floor_ids=[1],
        fairness_band=0,
        max_one_per_day=False,
        time_limit_s=5.0,
        max_extra_duties=0,
    )
    result = solver.solve()

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert result.loads[1] == 1
    assert result.loads[2] == 1
    assert result.band_violation == 0


def test_shortfall_detected_when_coverage_impossible() -> None:
    teachers = [
        _make_teacher(1, target=0, periods_by_day={}),
    ]
    slot = _make_slot(
        "slot-1",
        dt=date(2024, 9, 2),
        break_index=2,
        before_period=2,
        after_period=3,
        needs={1: 2},
    )

    solver = BreakSupervisionSolver(
        teachers=teachers,
        break_slots=[slot],
        floor_ids=[1],
        fairness_band=None,
        max_one_per_day=True,
        time_limit_s=5.0,
    )
    result = solver.solve()

    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert result.total_shortfall == 2
    assert result.shortfalls == {("slot-1", 1): 2}
