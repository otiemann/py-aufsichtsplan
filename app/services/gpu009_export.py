from __future__ import annotations

from datetime import date
from typing import List

from sqlalchemy.orm import Session

from ..models import DutySlot, Assignment, Teacher, Floor


# Hinweis: Das genaue GPU009-Format kann je nach System variieren.
# Diese Implementierung erzeugt eine schlanke, leicht anpassbare Textdatei:
# YYYY-MM-DD;BREAK;FLOOR;TEACHER_ABBR_OR_NAME

def generate_gpu009(db: Session, start_date: date, end_date: date) -> str:
    lines: List[str] = []

    q = (
        db.query(DutySlot, Floor)
        .join(Floor, Floor.id == DutySlot.floor_id)
        .filter(DutySlot.date >= start_date, DutySlot.date <= end_date)
        .order_by(DutySlot.date, DutySlot.break_index, Floor.name)
        .all()
    )

    for slot, floor in q:
        assignments = (
            db.query(Assignment, Teacher)
            .join(Teacher, Teacher.id == Assignment.teacher_id)
            .filter(Assignment.duty_slot_id == slot.id)
            .order_by(Teacher.last_name, Teacher.first_name)
            .all()
        )
        if assignments:
            for _, t in assignments:
                abbr = t.abbreviation or f"{t.last_name},{t.first_name}"
                lines.append(f"{slot.date.isoformat()};{slot.break_index};{floor.name};{abbr}")
        else:
            lines.append(f"{slot.date.isoformat()};{slot.break_index};{floor.name};")

    return "\n".join(lines) + "\n"
