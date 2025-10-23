from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import StreamingResponse, RedirectResponse, JSONResponse
from fastapi.requests import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from pydantic import BaseModel

from ..database import get_db
from ..models import Floor, DutySlot, Assignment, Teacher
from ..services.scheduler import generate_assignments
from ..services.pdf_export import generate_pdf, generate_pdf_by_floor
from ..services.gpu009_export import generate_gpu009

router = APIRouter()

# Templates aus main übernehmen, damit alle Router identische Loader nutzen
try:
    from ..main import templates as _main_templates
    templates = _main_templates  # type: ignore
except Exception:
    # Fallback (Dev)
    templates = Jinja2Templates(directory="app/templates")


# Pydantic Models für API
class TeacherAssignmentData(BaseModel):
    day: int
    break_index: int
    floor: str
    teachers: List[str]


class SaveChangesRequest(BaseModel):
    assignments: List[TeacherAssignmentData]


def monday_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def current_week_range() -> tuple[date, date]:
    start = monday_of_week(date.today())
    end = start + timedelta(days=4)
    return start, end


def weekday_labels() -> List[str]:
    return ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]


def break_labels() -> List[str]:
    return ["0. Stunde", "2./3. Stunde", "4./5. Stunde", "6./7. Stunde"]


def build_week_grid(db: Session, start_date: date, breaks_per_day: int) -> List[List[List[str]]]:
    floors = db.query(Floor).order_by(Floor.order_index, Floor.name).all()
    end_date = start_date + timedelta(days=4)

    teacher_day_counts: Dict[tuple[int, date], int] = {
        (teacher_id, duty_date): int(count)
        for teacher_id, duty_date, count in (
            db.query(Assignment.teacher_id, DutySlot.date, func.count(Assignment.id))
            .join(DutySlot, DutySlot.id == Assignment.duty_slot_id)
            .filter(DutySlot.date >= start_date, DutySlot.date <= end_date)
            .group_by(Assignment.teacher_id, DutySlot.date)
            .all()
        )
    }

    grid: List[List[List[str]]] = []
    for day_offset in range(5):
        d = start_date + timedelta(days=day_offset)
        weekday = d.weekday()
        row: List[List[str]] = []
        for b in range(1, breaks_per_day + 1):
            cell_lines: List[str] = []
            for f in floors:
                slot = (
                    db.query(DutySlot)
                    .filter(DutySlot.date == d, DutySlot.break_index == b, DutySlot.floor_id == f.id)
                    .one_or_none()
                )
                labels: List[str] = []
                if slot:
                    teachers = (
                        db.query(Teacher)
                        .join(Assignment, Assignment.teacher_id == Teacher.id)
                        .filter(Assignment.duty_slot_id == slot.id)
                        .order_by(Teacher.last_name, Teacher.first_name)
                        .all()
                    )
                    labels = []
                    for t in teachers:
                        label = t.abbreviation or f"{t.last_name}, {t.first_name}"
                        warn_reasons: List[str] = []
                        if not t.is_available_for_supervision(weekday, b):
                            warn_reasons.append("no-lesson")
                        if teacher_day_counts.get((t.id, slot.date), 0) > 1:
                            warn_reasons.append("double-duty")

                        warn_suffix = ""
                        if warn_reasons:
                            warn_suffix = "|warn:" + ";".join(warn_reasons)
                        labels.append(f"{label}{warn_suffix}")
                cell_lines.append(f"{f.name}: {', '.join(labels) if labels else '—'}")
            row.append(cell_lines)
        grid.append(row)
    return grid


def week_counts(db: Session, start_date: date, end_date: date) -> tuple[list[Dict], int]:
    rows = (
        db.query(
            Teacher,
            func.count(DutySlot.id).label("cnt"),
        )
        .outerjoin(Assignment, Assignment.teacher_id == Teacher.id)
        .outerjoin(
            DutySlot,
            and_(DutySlot.id == Assignment.duty_slot_id, DutySlot.date >= start_date, DutySlot.date <= end_date),
        )
        .group_by(Teacher.id)
        .order_by(Teacher.last_name, Teacher.first_name)
        .all()
    )
    out: List[Dict] = []
    total_assignments = 0
    for t, cnt in rows:
        count_int = int(cnt or 0)
        total_assignments += count_int
        target = t.quota.target_duties if t.quota else 0
        out.append({
            "abbreviation": t.abbreviation or "",
            "first_name": t.first_name,
            "last_name": t.last_name,
            "count": count_int,
            "target": int(target or 0),
        })
    return out, total_assignments


@router.get("/floors")
async def floors_get(request: Request, db: Session = Depends(get_db)):
    floors = db.query(Floor).order_by(Floor.order_index, Floor.name).all()
    return templates.TemplateResponse("plan/floors.html", {"request": request, "floors": floors})


@router.post("/floors")
async def floors_post(
    request: Request,
    name: str = Form(...),
    required_per_break: int = Form(1),
    order_index: int = Form(0),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if name:
        f = db.query(Floor).filter(Floor.name == name).one_or_none()
        if f is None:
            db.add(Floor(name=name, required_per_break=max(1, required_per_break or 1), order_index=order_index or 0))
            db.commit()
    return RedirectResponse(url="/plan/floors", status_code=303)


@router.post("/floors/order")
async def floors_order(floor_id: int = Form(...), order_index: int = Form(0), db: Session = Depends(get_db)):
    f = db.get(Floor, floor_id)
    if f:
        f.order_index = int(order_index or 0)
        db.commit()
    return RedirectResponse(url="/plan/floors", status_code=303)


@router.post("/floors/delete")
async def floors_delete(floor_id: int = Form(...), db: Session = Depends(get_db)):
    f = db.get(Floor, floor_id)
    if f:
        db.delete(f)
        db.commit()
    return RedirectResponse(url="/plan/floors", status_code=303)


@router.get("/generate")
async def generate_get(request: Request, db: Session = Depends(get_db)):
    start_date, end_date = current_week_range()
    bpd = 4
    grid = build_week_grid(db, start_date, bpd)
    counts, total_assignments = week_counts(db, start_date, end_date)
    teachers = db.query(Teacher).order_by(Teacher.last_name, Teacher.first_name).all()
    teacher_options = [
        {
            "id": t.id,
            "abbreviation": t.abbreviation or "",
            "display": (t.abbreviation + " – " if t.abbreviation else "") + f"{t.last_name}, {t.first_name}",
            "value": t.abbreviation or f"{t.last_name}, {t.first_name}",
        }
        for t in teachers
    ]
    teacher_options_json = json.dumps(teacher_options, ensure_ascii=False)
    return templates.TemplateResponse(
        "plan/generate.html",
        {
            "request": request,
            "day_labels": weekday_labels(),
            "break_labels": break_labels(),
            "breaks_per_day": bpd,
            "grid": grid,
            "counts": counts,
            "total_assignments": total_assignments,
            "teacher_options_json": teacher_options_json,
        },
    )


@router.post("/generate")
async def generate_post(
    request: Request,
    db: Session = Depends(get_db),
):
    start_date, end_date = current_week_range()
    bpd = 4
    generate_assignments(db, start_date, end_date, bpd)
    grid = build_week_grid(db, start_date, bpd)
    counts, total_assignments = week_counts(db, start_date, end_date)
    teachers = db.query(Teacher).order_by(Teacher.last_name, Teacher.first_name).all()
    teacher_options = [
        {
            "id": t.id,
            "abbreviation": t.abbreviation or "",
            "display": (t.abbreviation + " – " if t.abbreviation else "") + f"{t.last_name}, {t.first_name}",
            "value": t.abbreviation or f"{t.last_name}, {t.first_name}",
        }
        for t in teachers
    ]
    teacher_options_json = json.dumps(teacher_options, ensure_ascii=False)
    return templates.TemplateResponse(
        "plan/week.html",
        {
            "request": request,
            "day_labels": weekday_labels(),
            "break_labels": break_labels(),
            "breaks_per_day": bpd,
            "grid": grid,
            "counts": counts,
            "total_assignments": total_assignments,
            "teacher_options_json": teacher_options_json,
        },
    )


@router.get("/export/pdf")
async def export_pdf(db: Session = Depends(get_db)):
    start_date, end_date = current_week_range()
    pdf_bytes = generate_pdf(db, start_date, end_date)
    return StreamingResponse(iter([pdf_bytes]), media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=aufsicht_woche.pdf"
    })


@router.get("/export/pdf-by-floor")
async def export_pdf_by_floor(db: Session = Depends(get_db)):
    start_date, end_date = current_week_range()
    pdf_bytes = generate_pdf_by_floor(db, start_date, end_date)
    return StreamingResponse(iter([pdf_bytes]), media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=aufsicht_nach_stockwerk.pdf"
    })


@router.get("/export/gpu009")
async def export_gpu009(db: Session = Depends(get_db)):
    start_date, end_date = current_week_range()
    text = generate_gpu009(db, start_date, end_date)
    return StreamingResponse(iter([text.encode("utf-8")]), media_type="text/plain; charset=utf-8", headers={
        "Content-Disposition": f"attachment; filename=GPU009_{start_date.isoformat()}_{end_date.isoformat()}.txt"
    })


@router.post("/save-changes")
async def save_changes(request: SaveChangesRequest, db: Session = Depends(get_db)):
    """Speichert manuelle Änderungen am Aufsichtsplan (Drag-and-Drop)"""
    try:
        start_date, end_date = current_week_range()
        
        # Erstelle Lookups für Floors und Teachers
        floors_by_name = {f.name: f for f in db.query(Floor).all()}
        teachers_by_name = {}
        
        # Erstelle Teacher-Lookup (Kürzel und vollständige Namen)
        for teacher in db.query(Teacher).all():
            if teacher.abbreviation:
                teachers_by_name[teacher.abbreviation] = teacher
            teachers_by_name[f"{teacher.last_name}, {teacher.first_name}"] = teacher
            # Auch für den Fall, dass nur der Name verwendet wird
            teachers_by_name[teacher.last_name] = teacher
        
        # Lösche alle existierenden Assignments und DutySlots für den aktuellen Zeitraum
        # Zuerst Assignments löschen (wegen Foreign Key Constraint)
        existing_slots = db.query(DutySlot).filter(
            DutySlot.date >= start_date,
            DutySlot.date <= end_date
        ).all()
        
        for slot in existing_slots:
            db.query(Assignment).filter(Assignment.duty_slot_id == slot.id).delete()
        
        # Dann DutySlots löschen
        db.query(DutySlot).filter(
            DutySlot.date >= start_date,
            DutySlot.date <= end_date
        ).delete()
        
        # Explizit committen, um sicherzustellen, dass die Löschung abgeschlossen ist
        db.commit()
        
        # Erstelle neue Assignments basierend auf den Drag-and-Drop-Änderungen
        created_assignments = 0
        
        for assignment_data in request.assignments:
            day_offset = assignment_data.day
            break_index = assignment_data.break_index
            floor_name = assignment_data.floor
            teacher_names = assignment_data.teachers
            
            # Überspringe leere Assignments
            if not teacher_names:
                continue
            
            # Finde oder erstelle Floor
            floor = floors_by_name.get(floor_name)
            if not floor:
                # Falls ein neues Stockwerk durch Drag-and-Drop entstanden ist
                floor = Floor(name=floor_name, required_per_break=1, order_index=len(floors_by_name))
                db.add(floor)
                db.flush()
                floors_by_name[floor_name] = floor
            
            # Berechne das Datum
            slot_date = start_date + timedelta(days=day_offset)
            
            # Prüfe, ob ein DutySlot mit diesen Parametern bereits existiert (Sicherheitscheck)
            existing_duty_slot = db.query(DutySlot).filter(
                DutySlot.date == slot_date,
                DutySlot.break_index == break_index,
                DutySlot.floor_id == floor.id
            ).first()
            
            if existing_duty_slot:
                duty_slot = existing_duty_slot
            else:
                # Erstelle DutySlot
                duty_slot = DutySlot(
                    date=slot_date,
                    break_index=break_index,
                    floor_id=floor.id
                )
                db.add(duty_slot)
                db.flush()
            
            # Erstelle Assignments für alle Lehrkräfte
            for teacher_name in teacher_names:
                teacher_name = teacher_name.strip()
                if not teacher_name:
                    continue
                
                teacher = teachers_by_name.get(teacher_name)
                if teacher:
                    assignment = Assignment(
                        duty_slot_id=duty_slot.id,
                        teacher_id=teacher.id
                    )
                    db.add(assignment)
                    created_assignments += 1
                else:
                    print(f"[WARN] Lehrkraft '{teacher_name}' nicht gefunden beim Speichern")
        
        # Committe alle Änderungen
        db.commit()
        
        return JSONResponse(content={
            "message": "Änderungen erfolgreich gespeichert",
            "created_assignments": created_assignments,
            "total_slots": len(request.assignments)
        })
        
    except Exception as e:
        db.rollback()
        print(f"[ERROR] Fehler beim Speichern der manuellen Änderungen: {e}")
        raise HTTPException(status_code=500, detail=f"Fehler beim Speichern: {str(e)}")
