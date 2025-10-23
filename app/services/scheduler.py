from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from datetime import date, timedelta
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Tuple, Type

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload

from ..models import Assignment, DutySlot, Floor, Teacher

if TYPE_CHECKING:
    from ortools.sat.python import cp_model
    from .cp_sat_solver import BreakSlotSpec, BreakSupervisionSolver, SolverResult, TeacherSpec

logger = logging.getLogger(__name__)


BREAK_PERIOD_MAPPING: Dict[int, Tuple[Optional[int], Optional[int]]] = {
    1: (None, 1),
    2: (2, 3),
    3: (4, 5),
    4: (6, 7),
}


def _parse_fairness_band() -> Optional[int]:
    raw = os.getenv("SCHEDULER_FAIRNESS_BAND", "").strip()
    if not raw:
        return 0
    if raw.lower() in {"none", "off", "false"}:
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Ungültiger Wert für SCHEDULER_FAIRNESS_BAND (%s). Verwende 0.", raw)
        return 0
    return max(0, value)


def _max_one_per_day_enabled() -> bool:
    raw = os.getenv("SCHEDULER_MAX_ONE_DUTY_PER_DAY", "")
    return raw.lower() in {"1", "true", "yes", "on"}


def _parse_time_limit() -> float:
    raw = os.getenv("SCHEDULER_TIME_LIMIT_SECONDS", "")
    if not raw:
        return 30.0
    try:
        return max(1.0, float(raw))
    except ValueError:
        logger.warning("Ungültiger Wert für SCHEDULER_TIME_LIMIT_SECONDS (%s). Verwende 30s.", raw)
        return 30.0


def _parse_num_workers() -> int:
    raw = os.getenv("SCHEDULER_NUM_WORKERS", "")
    if not raw:
        return 8
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("Ungültiger Wert für SCHEDULER_NUM_WORKERS (%s). Verwende 8.", raw)
        return 8


def daterange(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def is_weekday(day: date) -> bool:
    return day.weekday() < 5


def ensure_slots(
    db: Session,
    start_date: date,
    end_date: date,
    breaks_per_day: int,
) -> List[DutySlot]:
    floors: List[Floor] = db.query(Floor).order_by(Floor.id).all()
    slots: List[DutySlot] = []

    for current_date in daterange(start_date, end_date):
        if not is_weekday(current_date):
            continue
        for break_index in range(1, breaks_per_day + 1):
            for floor in floors:
                slot = (
                    db.query(DutySlot)
                    .filter(
                        DutySlot.date == current_date,
                        DutySlot.break_index == break_index,
                        DutySlot.floor_id == floor.id,
                    )
                    .one_or_none()
                )
                if slot is None:
                    slot = DutySlot(date=current_date, break_index=break_index, floor_id=floor.id)
                    db.add(slot)
                    db.flush()
                slots.append(slot)
    db.commit()
    return slots


def clear_assignments(db: Session, start_date: date, end_date: date) -> None:
    slot_ids = [
        s_id
        for (s_id,) in (
            db.query(DutySlot.id)
            .filter(DutySlot.date >= start_date, DutySlot.date <= end_date)
            .all()
        )
    ]
    if not slot_ids:
        return

    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            (
                db.query(Assignment)
                .filter(Assignment.duty_slot_id.in_(slot_ids))
                .delete(synchronize_session=False)
            )
            db.commit()
            return
        except OperationalError:
            db.rollback()
            if attempt == max_attempts:
                raise
            time.sleep(0.1 * attempt)


def _break_periods(break_index: int) -> Tuple[Optional[int], Optional[int]]:
    return BREAK_PERIOD_MAPPING.get(break_index, (None, None))


def _collect_day_periods(teacher: Teacher) -> Dict[int, frozenset[int]]:
    day_map: Dict[int, set[int]] = defaultdict(set)
    for lesson in teacher.lessons:
        if lesson.weekday is None or lesson.hour is None:
            continue
        if 0 <= lesson.weekday <= 6:
            day_map[int(lesson.weekday)].add(int(lesson.hour))
    return {day: frozenset(hours) for day, hours in day_map.items()}


def _build_teacher_specs(
    teachers: Iterable[Teacher],
    teacher_spec_cls: Type["TeacherSpec"],
) -> List["TeacherSpec"]:
    specs: List["TeacherSpec"] = []
    for teacher in teachers:
        target = 0
        if teacher.quota and teacher.quota.target_duties:
            target = max(0, int(teacher.quota.target_duties))

        prio = getattr(teacher, "prio_rank", None)
        if prio is None:
            prio = getattr(teacher, "priority_rank", None)
        try:
            prio_value = int(prio) if prio is not None else 10
        except (TypeError, ValueError):
            prio_value = 10

        floor_weights = getattr(teacher, "floor_weights", None)
        if not isinstance(floor_weights, dict):
            floor_weights = None

        specs.append(
            teacher_spec_cls(
                id=teacher.id,
                target=target,
                prio_rank=prio_value,
                preferred_floor=teacher.preferred_floor_id,
                floor_weights=floor_weights,
                day_periods=_collect_day_periods(teacher),
            )
        )
    return specs


def _build_break_slots(
    start_date: date,
    end_date: date,
    floors: Iterable[Floor],
    breaks_per_day: int,
    break_slot_cls: Type["BreakSlotSpec"],
) -> List["BreakSlotSpec"]:
    slots: List["BreakSlotSpec"] = []
    floor_requirements = {
        floor.id: max(0, floor.required_per_break or 0)
        for floor in floors
    }

    for current_date in daterange(start_date, end_date):
        if not is_weekday(current_date):
            continue
        day_index = current_date.weekday()
        for break_index in range(1, breaks_per_day + 1):
            before_period, after_period = _break_periods(break_index)
            needs = {floor_id: need for floor_id, need in floor_requirements.items() if need > 0}
            if not needs:
                continue
            slot_id = f"{current_date.isoformat()}#{break_index}"
            slots.append(
                break_slot_cls(
                    slot_id=slot_id,
                    date=current_date,
                    day_index=day_index,
                    break_index=break_index,
                    before_period=before_period,
                    after_period=after_period,
                    needs=needs,
                )
            )
    return slots
    floor_requirements = {
        floor.id: max(0, floor.required_per_break or 0)
        for floor in floors
    }

    for current_date in daterange(start_date, end_date):
        if not is_weekday(current_date):
            continue
        day_index = current_date.weekday()
        for break_index in range(1, breaks_per_day + 1):
            before_period, after_period = _break_periods(break_index)
            needs = {floor_id: need for floor_id, need in floor_requirements.items() if need > 0}
            if not needs:
                continue
            slot_id = f"{current_date.isoformat()}#{break_index}"
            slots.append(
                BreakSlotSpec(
                    slot_id=slot_id,
                    date=current_date,
                    day_index=day_index,
                    break_index=break_index,
                    before_period=before_period,
                    after_period=after_period,
                    needs=needs,
                )
            )
    return slots


def _log_shortages(
    solver: "BreakSupervisionSolver",
    floors_by_id: Dict[int, Floor],
) -> None:
    for (slot_id, floor_id), eligible in solver.eligible_counts.items():
        slot = solver.slot_lookup.get(slot_id)
        if not slot:
            continue
        need = slot.needs.get(floor_id, 0)
        if need <= eligible:
            continue
        floor = floors_by_id.get(floor_id)
        floor_label = floor.name if floor else f"ID {floor_id}"
        logger.error(
            "Zu wenige Kandidaten für %s (Wochentag %s, Pause %s, Stockwerk %s): Bedarf %s, Eligible %s.",
            slot.date,
            slot.day_index,
            slot.break_index,
            floor_label,
            need,
            eligible,
        )


def generate_assignments(
    db: Session,
    start_date: date,
    end_date: date,
    breaks_per_day: int,
) -> None:
    try:
        from ortools.sat.python import cp_model  # type: ignore
        from .cp_sat_solver import (
            BreakSlotSpec,
            BreakSupervisionSolver,
            TeacherSpec,
        )
    except ModuleNotFoundError as exc:
        logger.error(
            "CP-SAT Scheduler nicht verfügbar: %s. Bitte ortools installieren (z.B. via 'pip install ortools').",
            exc,
        )
        return

    floors: List[Floor] = db.query(Floor).order_by(Floor.order_index, Floor.name).all()
    if not floors:
        logger.warning("Keine Stockwerke definiert – Abbruch der Planung.")
        return

    duty_slots = ensure_slots(db, start_date, end_date, breaks_per_day)
    clear_assignments(db, start_date, end_date)

    floors_by_id: Dict[int, Floor] = {floor.id: floor for floor in floors}

    teachers: List[Teacher] = (
        db.query(Teacher)
        .options(
            selectinload(Teacher.lessons),
            selectinload(Teacher.quota),
            selectinload(Teacher.preferred_floor),
        )
        .filter(Teacher.exempt == False)  # noqa: E712 - SQLAlchemy uses bool comparison
        .order_by(Teacher.last_name, Teacher.first_name)
        .all()
    )

    if not teachers:
        logger.warning("Keine geeigneten Lehrkräfte mit Soll-Aufsichten gefunden.")
        return

    teacher_specs = _build_teacher_specs(teachers, TeacherSpec)
    break_slots = _build_break_slots(start_date, end_date, floors, breaks_per_day, BreakSlotSpec)

    if not break_slots:
        logger.info("Keine Break-Slots mit Bedarf im angegebenen Zeitraum.")
        return

    fairness_band = _parse_fairness_band()
    max_one_per_day = _max_one_per_day_enabled()
    time_limit = _parse_time_limit()
    num_workers = _parse_num_workers()

    solver = BreakSupervisionSolver(
        teachers=teacher_specs,
        break_slots=break_slots,
        floor_ids=[floor_id for floor_id, need in {f.id: f.required_per_break for f in floors}.items() if need],
        fairness_band=fairness_band,
        max_one_per_day=max_one_per_day,
        time_limit_s=time_limit,
        num_workers=num_workers,
    )

    result = solver.solve()

    if result.status_enum not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        logger.error("Planung fehlgeschlagen (Solver-Status: %s).", result.status)
        _log_shortages(solver, floors_by_id)
        return

    duty_slot_lookup: Dict[Tuple[date, int, int], DutySlot] = {
        (slot.date, slot.break_index, slot.floor_id): slot
        for slot in duty_slots
    }

    assignments_created = 0
    for assignment in result.assignments:
        duty_slot = duty_slot_lookup.get((assignment.date, assignment.break_index, assignment.floor_id))
        if duty_slot is None:
            logger.warning(
                "Kein DutySlot für Zuweisung gefunden (%s, Pause %s, Floor %s).",
                assignment.date,
                assignment.break_index,
                assignment.floor_id,
            )
            continue
        db.add(Assignment(duty_slot_id=duty_slot.id, teacher_id=assignment.teacher_id))
        assignments_created += 1
    db.commit()

    logger.info(
        "Aufsichtsplan erstellt: %s Aufsichten, max_dev=%s, total_dev=%s, daily_excess=%s, priority_cost=%s, Solver=%s (%.2fs).",
        assignments_created,
        result.max_dev,
        result.total_dev,
        result.daily_excess,
        result.priority_cost,
        result.status,
        result.wall_time_seconds,
    )

    if assignments_created == 0:
        _log_shortages(solver, floors_by_id)

    if logger.isEnabledFor(logging.DEBUG):
        teacher_map = {t.id: t for t in teachers}
        for teacher_id, load in sorted(result.loads.items(), key=lambda item: teacher_map[item[0]].abbreviation or teacher_map[item[0]].last_name):
            teacher = teacher_map[teacher_id]
            target = teacher.quota.target_duties if teacher.quota else 0
            logger.debug(
                "Lehrkraft %s (%s %s): Load=%s, Soll=%s",
                teacher.abbreviation or teacher.id,
                teacher.last_name,
                teacher.first_name,
                load,
                target,
            )
