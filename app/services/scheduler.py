from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Tuple, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
import random

from ..models import Teacher, TeacherQuota, Floor, DutySlot, Assignment


def daterange(start: date, end: date) -> List[date]:
    current = start
    out: List[date] = []
    while current <= end:
        out.append(current)
        current += timedelta(days=1)
    return out


def is_weekday(d: date) -> bool:
    # Monday=0 .. Sunday=6
    return d.weekday() < 5


def ensure_slots(
    db: Session,
    start_date: date,
    end_date: date,
    breaks_per_day: int,
) -> List[DutySlot]:
    floors: List[Floor] = db.query(Floor).order_by(Floor.id).all()
    slots: List[DutySlot] = []

    for d in daterange(start_date, end_date):
        if not is_weekday(d):
            continue
        for b in range(1, breaks_per_day + 1):
            for f in floors:
                slot = (
                    db.query(DutySlot)
                    .filter(DutySlot.date == d, DutySlot.break_index == b, DutySlot.floor_id == f.id)
                    .one_or_none()
                )
                if slot is None:
                    slot = DutySlot(date=d, break_index=b, floor_id=f.id)
                    db.add(slot)
                    db.flush()
                slots.append(slot)
    db.commit()
    return slots


def clear_assignments(db: Session, start_date: date, end_date: date) -> None:
    slot_ids = [
        s_id
        for (s_id,) in db.query(DutySlot.id)
        .filter(DutySlot.date >= start_date, DutySlot.date <= end_date)
        .all()
    ]
    if slot_ids:
        db.query(Assignment).filter(Assignment.duty_slot_id.in_(slot_ids)).delete(synchronize_session=False)
        db.commit()


def generate_assignments(
    db: Session,
    start_date: date,
    end_date: date,
    breaks_per_day: int,
) -> None:
    slots = ensure_slots(db, start_date, end_date, breaks_per_day)
    clear_assignments(db, start_date, end_date)

    teachers = (
        db.query(Teacher)
        .join(TeacherQuota, isouter=True)
        .filter(Teacher.exempt == False)
        .all()
    )

    eligible: List[Tuple[Teacher, int]] = []
    teacher_to_target: Dict[int, int] = {}

    for t in teachers:
        target = t.quota.target_duties if t.quota else 0
        if target and target > 0:
            teacher_to_target[t.id] = target
            eligible.append((t, target))

    if not eligible:
        return

    # zufälliger Bias pro Lehrkraft für diese Planungsrunde
    random_bias: Dict[int, float] = {t.id: random.random() for t, _ in eligible}

    existing_counts: Dict[int, int] = {t.id: 0 for t, _ in eligible}

    q = (
        db.query(Assignment.teacher_id, func.count(Assignment.id))
        .join(DutySlot, DutySlot.id == Assignment.duty_slot_id)
        .filter(DutySlot.date >= start_date, DutySlot.date <= end_date)
        .group_by(Assignment.teacher_id)
        .all()
    )
    for teacher_id, cnt in q:
        if teacher_id in existing_counts:
            existing_counts[teacher_id] = cnt

    def pick_teacher(d: date, break_index: int, floor_id: int) -> Optional[Teacher]:
        # Berechne Wochentag (0=Montag, 4=Freitag)
        weekday = d.weekday()
        
        already = set(
            t_id
            for (t_id,) in db.query(Assignment.teacher_id)
            .join(DutySlot, DutySlot.id == Assignment.duty_slot_id)
            .filter(DutySlot.date == d, DutySlot.break_index == break_index)
            .all()
        )

        # Kandidaten unter Soll
        candidates_under: List[Tuple[Teacher, int]] = []
        candidates_all: List[Tuple[Teacher, int]] = []
        for t, target in eligible:
            if t.id in already:
                continue
            
            # Prüfe Anwesenheit an diesem Wochentag
            if not t.is_available_on_weekday(weekday):
                continue
                
            assigned = existing_counts.get(t.id, 0)
            if assigned >= target:
                continue
            tuple_val = (t, target)
            if t.preferred_floor_id == floor_id:
                candidates_under.append(tuple_val)
            else:
                candidates_all.append(tuple_val)

        pool = candidates_under if candidates_under else candidates_all
        if not pool:
            return None

        best_t = None
        best_key = None
        for t, target in pool:
            assigned = existing_counts.get(t.id, 0)
            ratio = assigned / max(target, 1)
            key = (ratio, assigned, random_bias[t.id])
            if best_key is None or key < best_key:
                best_key = key
                best_t = t
        return best_t

    floor_required: Dict[int, int] = {}
    for f in db.query(Floor).all():
        floor_required[f.id] = max(1, f.required_per_break or 1)

    for slot in slots:
        current_assigned = (
            db.query(Assignment)
            .filter(Assignment.duty_slot_id == slot.id)
            .count()
        )
        needed = max(0, floor_required.get(slot.floor_id, 1) - current_assigned)
        for _ in range(needed):
            t = pick_teacher(slot.date, slot.break_index, slot.floor_id)
            if t is None:
                break
            db.add(Assignment(duty_slot_id=slot.id, teacher_id=t.id))
            existing_counts[t.id] = existing_counts.get(t.id, 0) + 1
        db.flush()

    db.commit()
