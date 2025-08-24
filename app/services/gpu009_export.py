from __future__ import annotations

from datetime import date
from typing import List

from sqlalchemy.orm import Session

from ..models import DutySlot, Assignment, Teacher, Floor


# GPU009-Format für Schulportal Hessen:
# "STOCKWERK";"KÜRZEL";WOCHENTAG;PAUSENINDEX;1;
# WOCHENTAG: 1=Montag, 2=Dienstag, 3=Mittwoch, 4=Donnerstag, 5=Freitag
# PAUSENINDEX: 1, 3, 5, 7 (Aufsicht VOR 1., 3., 5., 7. Stunde)

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
        # Konvertiere Datum zu Wochentag (1=Montag, 2=Dienstag, etc.)
        weekday = slot.date.weekday() + 1  # Python: 0=Montag -> GPU009: 1=Montag
        
        # Konvertiere break_index zu GPU009-Format
        # break_index 1 -> 1 (vor 1. Stunde)
        # break_index 2 -> 3 (vor 3. Stunde) 
        # break_index 3 -> 5 (vor 5. Stunde)
        # break_index 4 -> 7 (vor 7. Stunde)
        gpu_break_index = (slot.break_index - 1) * 2 + 1

        assignments = (
            db.query(Assignment, Teacher)
            .join(Teacher, Teacher.id == Assignment.teacher_id)
            .filter(Assignment.duty_slot_id == slot.id)
            .order_by(Teacher.last_name, Teacher.first_name)
            .all()
        )
        
        if assignments:
            for _, t in assignments:
                abbr = t.abbreviation or f"{t.last_name[0:3].upper()}"
                lines.append(f'"{floor.name}";"{abbr}";{weekday};{gpu_break_index};1;')

    return "\n".join(lines) + "\n"
