from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import StreamingResponse, RedirectResponse, JSONResponse
from fastapi.requests import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.exc import OperationalError
from sqlalchemy import and_, func, select
from pydantic import BaseModel

from ..database import get_db, Base, engine
from ..models import Floor, DutySlot, Assignment, Teacher, AppSetting, TeacherLesson
from ..services.scheduler import generate_repeating_period_assignments
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


PLAN_PERIOD_START_KEY = "plan_period_start"
PLAN_PERIOD_WEEKS_KEY = "plan_period_weeks"
DEFAULT_PLAN_WEEKS = 6


def _get_setting(db: Session, key: str) -> Optional[str]:
    try:
        row = db.query(AppSetting).filter(AppSetting.key == key).one_or_none()
        return row.value if row else None
    except OperationalError:
        # Tabelle fehlt (z.B. alte DB). Nachziehen und Default verwenden.
        try:
            # Rollback to clear failed session state before re-querying.
            db.rollback()
            Base.metadata.create_all(bind=engine)
            row = db.query(AppSetting).filter(AppSetting.key == key).one_or_none()
            return row.value if row else None
        except Exception:
            return None


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == key).one_or_none()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))
    db.commit()


def normalize_lesson_weekdays(db: Session) -> bool:
    """Normalisiert TeacherLesson.weekday auf 0=Mo..4=Fr, falls alte Daten 1..5 verwenden."""
    stats = db.query(
        func.min(TeacherLesson.weekday),
        func.max(TeacherLesson.weekday),
        func.count(TeacherLesson.weekday),
    ).one()
    if not stats:
        return False
    min_wd, max_wd, total = stats
    if total == 0:
        return False
    zero_based = db.query(TeacherLesson).filter(TeacherLesson.weekday.between(0, 4)).count()
    one_based = db.query(TeacherLesson).filter(TeacherLesson.weekday.between(1, 5)).count()
    # Shift nur, wenn keine 0-basierten Werte existieren, aber 1-basierte vorhanden sind.
    if zero_based == 0 and one_based > 0 and min_wd is not None and max_wd is not None and max_wd <= 5 and min_wd >= 1:
        db.query(TeacherLesson).update({TeacherLesson.weekday: TeacherLesson.weekday - 1})
        db.commit()
        return True
    return False


def check_scheduler_available() -> Optional[str]:
    """Prüft ob OR-Tools (CP-SAT) verfügbar ist. Gibt Fehlermeldung zurück, falls nicht."""
    try:
        from ortools.sat.python import cp_model  # noqa: F401
        return None
    except Exception as exc:  # pragma: no cover - runtime env check
        return str(exc)


def get_planning_period(db: Session) -> tuple[date, date, int]:
    raw_start = _get_setting(db, PLAN_PERIOD_START_KEY)
    raw_weeks = _get_setting(db, PLAN_PERIOD_WEEKS_KEY)

    today = date.today()
    default_start = monday_of_week(today)
    if today.weekday() >= 5:
        default_start = default_start + timedelta(days=7)

    start = default_start
    if raw_start:
        try:
            start = datetime.fromisoformat(raw_start).date()
        except ValueError:
            start = default_start

    weeks = DEFAULT_PLAN_WEEKS
    if raw_weeks:
        try:
            weeks = int(raw_weeks)
        except ValueError:
            weeks = DEFAULT_PLAN_WEEKS
    weeks = min(12, max(1, weeks))

    start = monday_of_week(start)
    end = start + timedelta(days=(weeks * 7) - 1)
    return start, end, weeks


@router.post("/period")
async def set_planning_period(
    start_date: str = Form(...),
    weeks: int = Form(DEFAULT_PLAN_WEEKS),
    db: Session = Depends(get_db),
):
    try:
        parsed = datetime.fromisoformat(start_date).date()
    except ValueError:
        response = RedirectResponse(url="/plan/generate", status_code=303)
        response.set_cookie("flash", "Ungültiges Startdatum – bitte ein Datum auswählen.")
        return response

    weeks = min(12, max(1, int(weeks)))

    normalized = monday_of_week(parsed)
    _set_setting(db, PLAN_PERIOD_START_KEY, normalized.isoformat())
    _set_setting(db, PLAN_PERIOD_WEEKS_KEY, str(weeks))

    suffix = ""
    if normalized != parsed:
        suffix = " (auf Montag korrigiert)"

    response = RedirectResponse(url="/plan/generate", status_code=303)
    response.set_cookie(
        "flash",
        f"Zeitraum gespeichert: {weeks} Wochen ab {normalized.strftime('%d.%m.%Y')}{suffix}.",
    )
    return response


def weekday_labels() -> List[str]:
    return ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]


def break_labels() -> List[str]:
    return ["0. Stunde", "2./3. Stunde", "4./5. Stunde", "6./7. Stunde"]


def _day_label(day_offset: int) -> str:
    labels = weekday_labels()
    if 0 <= day_offset < len(labels):
        return labels[day_offset]
    return f"Tag {day_offset + 1}"


def _break_label(break_index: int) -> str:
    labels = break_labels()
    if 1 <= break_index <= len(labels):
        return labels[break_index - 1]
    return f"Pause {break_index}"


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
            "exempt": bool(t.exempt),
        })
    return out, total_assignments


def _teacher_option_value(teacher: Teacher) -> str:
    return teacher.abbreviation or f"{teacher.last_name}, {teacher.first_name}"


def build_teacher_options(teachers: List[Teacher]) -> List[Dict[str, Any]]:
    options: List[Dict[str, Any]] = []
    for teacher in teachers:
        value = _teacher_option_value(teacher)
        availability = {
            f"{weekday}-{break_index}": teacher.is_available_for_supervision(weekday, break_index)
            for weekday in range(5)
            for break_index in range(1, 5)
        }
        options.append(
            {
                "id": teacher.id,
                "abbreviation": teacher.abbreviation or "",
                "display": (teacher.abbreviation + " – " if teacher.abbreviation else "")
                + f"{teacher.last_name}, {teacher.first_name}",
                "value": value,
                "availability": availability,
                "exempt": bool(teacher.exempt),
            }
        )
    return options


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
    period_start, period_end, weeks = get_planning_period(db)
    template_end = period_start + timedelta(days=4)
    bpd = 4
    grid = build_week_grid(db, period_start, bpd)
    counts, total_template_assignments = week_counts(db, period_start, template_end)
    teachers = (
        db.query(Teacher)
        .filter(Teacher.exempt == False)  # noqa: E712
        .options(selectinload(Teacher.lessons))
        .order_by(Teacher.last_name, Teacher.first_name)
        .all()
    )
    teacher_options = build_teacher_options(teachers)
    teacher_options_json = json.dumps(teacher_options, ensure_ascii=False)
    return templates.TemplateResponse(
        "plan/generate.html",
        {
            "request": request,
            "period_start_iso": period_start.isoformat(),
            "period_start_label": period_start.strftime("%d.%m.%Y"),
            "period_end_label": period_end.strftime("%d.%m.%Y"),
            "period_weeks": weeks,
            "day_labels": weekday_labels(),
            "break_labels": break_labels(),
            "breaks_per_day": bpd,
            "grid": grid,
            "counts": counts,
            "total_assignments": total_template_assignments,
            "total_assignments_period": total_template_assignments * weeks,
            "teacher_options_json": teacher_options_json,
        },
    )


@router.post("/generate")
async def generate_post(
    request: Request,
    db: Session = Depends(get_db),
):
    ortools_error = check_scheduler_available()
    if ortools_error:
        response = RedirectResponse(url="/plan/generate", status_code=303)
        response.set_cookie(
            "flash",
            "Planer konnte nicht gestartet werden (OR-Tools fehlt oder ist nicht kompatibel). "
            "Bitte Python 3.11/3.12 verwenden oder OR-Tools neu installieren. "
            f"Details: {ortools_error}",
            max_age=15,
        )
        return response
    normalize_lesson_weekdays(db)
    period_start, period_end, weeks = get_planning_period(db)
    template_end = period_start + timedelta(days=4)
    bpd = 4
    generation_duration_seconds = 0.0
    try:
        start_ts = time.perf_counter()
        created_total = generate_repeating_period_assignments(db, period_start, weeks, bpd)
        generation_duration_seconds = max(0.0, time.perf_counter() - start_ts)
    except OperationalError:
        response = RedirectResponse(url="/plan/generate", status_code=303)
        response.set_cookie(
            "flash",
            "Datenbank ist aktuell gesperrt. Bitte schließen Sie ggf. weitere geöffnete Instanzen und versuchen Sie es erneut.",
            max_age=10,
        )
        return response
    grid = build_week_grid(db, period_start, bpd)
    counts, total_template_assignments = week_counts(db, period_start, template_end)
    teachers = (
        db.query(Teacher)
        .filter(Teacher.exempt == False)  # noqa: E712
        .options(selectinload(Teacher.lessons))
        .order_by(Teacher.last_name, Teacher.first_name)
        .all()
    )
    teacher_options = build_teacher_options(teachers)
    teacher_options_json = json.dumps(teacher_options, ensure_ascii=False)
    if created_total < 0:
        response = RedirectResponse(url="/plan/generate", status_code=303)
        response.set_cookie(
            "flash",
            "Planung fehlgeschlagen: Der Solver ist abgestürzt (OR-Tools). "
            "Bitte Aufsichtsplan.exe neu herunterladen/aktualisieren oder Python 3.11/3.12 verwenden. "
            "Details siehe app.log.",
            max_age=20,
        )
        return response
    if created_total <= 0:
        total_teachers = db.query(Teacher).filter(Teacher.exempt == False).count()  # noqa: E712
        total_lessons = db.query(TeacherLesson).count()
        relevant_lessons = (
            db.query(TeacherLesson)
            .filter(TeacherLesson.hour.in_([1, 2, 3, 4, 5, 6, 7]))
            .count()
        )
        teachers_with_lessons = db.query(TeacherLesson.teacher_id).distinct().count()
        active_teachers_with_lessons = (
            db.query(TeacherLesson.teacher_id)
            .join(Teacher, Teacher.id == TeacherLesson.teacher_id)
            .filter(Teacher.exempt == False)  # noqa: E712
            .distinct()
            .count()
        )
        active_teachers_with_relevant_lessons = (
            db.query(TeacherLesson.teacher_id)
            .join(Teacher, Teacher.id == TeacherLesson.teacher_id)
            .filter(Teacher.exempt == False)  # noqa: E712
            .filter(TeacherLesson.hour.in_([1, 2, 3, 4, 5, 6, 7]))
            .distinct()
            .count()
        )
        lessons_wd_0_4 = db.query(TeacherLesson).filter(TeacherLesson.weekday.between(0, 4)).count()
        lessons_wd_1_5 = db.query(TeacherLesson).filter(TeacherLesson.weekday.between(1, 5)).count()
        total_need = sum(f.required_per_break or 0 for f in db.query(Floor).all()) * 5 * bpd
        response = RedirectResponse(url="/plan/generate", status_code=303)
        response.set_cookie(
            "flash",
            "Plan leer. Prüfen Sie Unterrichts-Import/Kürzel und Bedarf. "
            f"Lehrkräfte aktiv: {total_teachers}, Stunden gesamt: {total_lessons}, "
            f"relevante Stunden (1-7): {relevant_lessons}, Lehrkräfte mit Stunden: {teachers_with_lessons}, "
            f"aktive Lehrkräfte mit Stunden: {active_teachers_with_lessons}, "
            f"aktive Lehrkräfte mit relevanten Stunden: {active_teachers_with_relevant_lessons}, "
            f"Wochentage 0-4: {lessons_wd_0_4}, Wochentage 1-5: {lessons_wd_1_5}, "
            f"Bedarf/Woche: {total_need}.",
            max_age=15,
        )
        return response
    return templates.TemplateResponse(
        "plan/week.html",
        {
            "request": request,
            "period_start_label": period_start.strftime("%d.%m.%Y"),
            "period_end_label": period_end.strftime("%d.%m.%Y"),
            "period_weeks": weeks,
            "generation_duration_label": f"{generation_duration_seconds:.1f}".replace(".", ","),
            "day_labels": weekday_labels(),
            "break_labels": break_labels(),
            "breaks_per_day": bpd,
            "grid": grid,
            "counts": counts,
            "total_assignments": total_template_assignments,
            "total_assignments_period": total_template_assignments * weeks,
            "teacher_options_json": teacher_options_json,
        },
    )


@router.get("/export/pdf")
async def export_pdf(db: Session = Depends(get_db)):
    start_date, end_date, _weeks = get_planning_period(db)
    pdf_bytes = generate_pdf(db, start_date, end_date)
    return StreamingResponse(iter([pdf_bytes]), media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=aufsicht_{start_date.isoformat()}_{end_date.isoformat()}.pdf"
    })


@router.get("/export/pdf-by-floor")
async def export_pdf_by_floor(db: Session = Depends(get_db)):
    start_date, end_date, _weeks = get_planning_period(db)
    pdf_bytes = generate_pdf_by_floor(db, start_date, end_date)
    return StreamingResponse(iter([pdf_bytes]), media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=aufsicht_nach_stockwerk_{start_date.isoformat()}_{end_date.isoformat()}.pdf"
    })


@router.get("/export/gpu009")
async def export_gpu009(db: Session = Depends(get_db)):
    start_date, end_date, _weeks = get_planning_period(db)
    text = generate_gpu009(db, start_date, end_date)
    return StreamingResponse(iter([text.encode("utf-8")]), media_type="text/plain; charset=utf-8", headers={
        "Content-Disposition": f"attachment; filename=GPU009_{start_date.isoformat()}_{end_date.isoformat()}.txt"
    })


@router.post("/save-changes")
async def save_changes(request: SaveChangesRequest, db: Session = Depends(get_db)):
    """Speichert manuelle Änderungen am Aufsichtsplan (Drag-and-Drop)"""
    try:
        period_start, period_end, weeks = get_planning_period(db)
        
        # Erstelle Lookups für Floors und Teachers
        floors_by_name = {f.name: f for f in db.query(Floor).all()}
        teachers_by_name = {}
        
        # Erstelle Teacher-Lookup (Kürzel und vollständige Namen)
        all_teachers = (
            db.query(Teacher)
            .options(selectinload(Teacher.lessons), selectinload(Teacher.quota))
            .all()
        )
        for teacher in all_teachers:
            if teacher.abbreviation:
                teachers_by_name[teacher.abbreviation] = teacher
            teachers_by_name[f"{teacher.last_name}, {teacher.first_name}"] = teacher
            # Auch für den Fall, dass nur der Name verwendet wird
            teachers_by_name[teacher.last_name] = teacher

        # Validierung (Wochen-Template): keine Doppelbelegung in derselben Pause
        # und kein Überschreiten des Solls.
        exempt_conflicts: List[str] = []
        duplicate_slot_conflicts: List[str] = []
        teacher_week_counts: Dict[int, int] = {}
        seen_teacher_slot_keys: set[tuple[int, int, int]] = set()

        for assignment_data in request.assignments:
            day_offset = int(assignment_data.day)
            break_index = int(assignment_data.break_index)
            floor_name = (assignment_data.floor or "").strip() or "?"
            if day_offset < 0 or day_offset > 4:
                continue
            if break_index < 1 or break_index > 4:
                continue

            for teacher_name in assignment_data.teachers or []:
                teacher_key = (teacher_name or "").strip()
                if not teacher_key:
                    continue
                teacher = teachers_by_name.get(teacher_key)
                if not teacher:
                    continue
                if teacher.exempt:
                    display_name = teacher.abbreviation or f"{teacher.last_name}, {teacher.first_name}"
                    day_label = _day_label(day_offset)
                    break_label = _break_label(break_index)
                    exempt_conflicts.append(
                        f"{display_name} ({day_label}, {break_label}, Stockwerk {floor_name})"
                    )
                    continue

                teacher_slot_key = (day_offset, break_index, int(teacher.id))
                if teacher_slot_key in seen_teacher_slot_keys:
                    display_name = teacher.abbreviation or f"{teacher.last_name}, {teacher.first_name}"
                    day_label = _day_label(day_offset)
                    break_label = _break_label(break_index)
                    duplicate_slot_conflicts.append(
                        f"{display_name} ({day_label}, {break_label}, Stockwerk {floor_name})"
                    )
                else:
                    seen_teacher_slot_keys.add(teacher_slot_key)

                teacher_week_counts[int(teacher.id)] = teacher_week_counts.get(int(teacher.id), 0) + 1

        quota_conflicts: List[str] = []
        for teacher in all_teachers:
            target = int(teacher.quota.target_duties or 0) if teacher.quota else 0
            if target <= 0:
                continue
            current_count = teacher_week_counts.get(int(teacher.id), 0)
            if current_count > target:
                display_name = teacher.abbreviation or f"{teacher.last_name}, {teacher.first_name}"
                quota_conflicts.append(f"{display_name} ({current_count}/{target})")

        if exempt_conflicts or duplicate_slot_conflicts or quota_conflicts:
            conflict_parts: List[str] = []
            if exempt_conflicts:
                preview = "; ".join(exempt_conflicts[:8])
                suffix = " …" if len(exempt_conflicts) > 8 else ""
                conflict_parts.append(
                    "Lehrkraft ist als 'keine Aufsicht' markiert: " + preview + suffix
                )
            if duplicate_slot_conflicts:
                preview = "; ".join(duplicate_slot_conflicts[:8])
                suffix = " …" if len(duplicate_slot_conflicts) > 8 else ""
                conflict_parts.append(
                    "Doppelbelegung gleiche Pause: " + preview + suffix
                )
            if quota_conflicts:
                preview = "; ".join(quota_conflicts[:8])
                suffix = " …" if len(quota_conflicts) > 8 else ""
                conflict_parts.append(
                    "Soll überschritten: " + preview + suffix
                )

            raise HTTPException(
                status_code=400,
                detail="Ungültige manuelle Zuordnung. " + " | ".join(conflict_parts),
            )

        # Validierung: Manuelle Änderungen dürfen nur verfügbare Lehrkräfte einplanen.
        invalid_assignments: List[str] = []
        for week_offset in range(weeks):
            for assignment_data in request.assignments:
                day_offset = int(assignment_data.day)
                break_index = int(assignment_data.break_index)
                slot_date = period_start + timedelta(days=(week_offset * 7) + day_offset)

                if slot_date > period_end:
                    continue

                if break_index < 1 or break_index > 4:
                    continue

                weekday = slot_date.weekday()
                for teacher_name in assignment_data.teachers or []:
                    teacher_key = (teacher_name or "").strip()
                    if not teacher_key:
                        continue
                    teacher = teachers_by_name.get(teacher_key)
                    if not teacher:
                        continue
                    if teacher.is_available_for_supervision(weekday, break_index):
                        continue

                    display_name = teacher.abbreviation or f"{teacher.last_name}, {teacher.first_name}"
                    day_label = _day_label(day_offset)
                    break_label = _break_label(break_index)
                    invalid_assignments.append(
                        f"{display_name} ({day_label}, {break_label}, {slot_date.strftime('%d.%m.%Y')})"
                    )

        if invalid_assignments:
            preview = "; ".join(invalid_assignments[:8])
            suffix = " …" if len(invalid_assignments) > 8 else ""
            raise HTTPException(
                status_code=400,
                detail=(
                    "Ungültige manuelle Zuordnung: Lehrkraft ist im Ziel-Slot nicht verfügbar "
                    "(Anwesenheit/Unterricht vor oder nach der Pause fehlt). "
                    f"Beispiele: {preview}{suffix}"
                ),
            )
        
        # Fehlende Floors einmalig anlegen (falls z.B. umbenannt/neu).
        for assignment_data in request.assignments:
            floor_name = (assignment_data.floor or "").strip()
            if not floor_name or floor_name in floors_by_name:
                continue
            floor = Floor(name=floor_name, required_per_break=1, order_index=len(floors_by_name))
            db.add(floor)
            db.flush()
            floors_by_name[floor_name] = floor
        db.commit()

        # Stelle sicher, dass DutySlots für den gesamten Zeitraum existieren.
        floors = list(floors_by_name.values())
        current_date = period_start
        while current_date <= period_end:
            if current_date.weekday() < 5:
                for break_index in range(1, 5):
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
                            db.add(DutySlot(date=current_date, break_index=break_index, floor_id=floor.id))
            current_date += timedelta(days=1)
        db.commit()

        # Lösche alle existierenden Assignments für den Zeitraum.
        slot_ids_subquery = select(DutySlot.id).where(
            DutySlot.date >= period_start, DutySlot.date <= period_end
        )
        db.query(Assignment).filter(Assignment.duty_slot_id.in_(slot_ids_subquery)).delete(
            synchronize_session=False
        )
        db.commit()

        # Lookup: (date, break_index, floor_id) -> duty_slot_id
        all_slots = (
            db.query(DutySlot)
            .filter(DutySlot.date >= period_start, DutySlot.date <= period_end)
            .all()
        )
        slot_lookup = {(s.date, int(s.break_index), int(s.floor_id)): int(s.id) for s in all_slots}
        
        # Erstelle neue Assignments basierend auf den Drag-and-Drop-Änderungen
        created_assignments = 0
        
        for week_offset in range(weeks):
            for assignment_data in request.assignments:
                day_offset = int(assignment_data.day)
                break_index = int(assignment_data.break_index)
                floor_name = (assignment_data.floor or "").strip()
                teacher_names = assignment_data.teachers or []

                floor = floors_by_name.get(floor_name)
                if not floor:
                    continue

                slot_date = period_start + timedelta(days=(week_offset * 7) + day_offset)
                if slot_date > period_end:
                    continue

                duty_slot_id = slot_lookup.get((slot_date, break_index, int(floor.id)))
                if duty_slot_id is None:
                    continue

                for teacher_name in teacher_names:
                    teacher_name = (teacher_name or "").strip()
                    if not teacher_name:
                        continue

                    teacher = teachers_by_name.get(teacher_name)
                    if teacher:
                        db.add(Assignment(duty_slot_id=duty_slot_id, teacher_id=teacher.id))
                        created_assignments += 1
                    else:
                        print(f"[WARN] Lehrkraft '{teacher_name}' nicht gefunden beim Speichern")
        
        # Committe alle Änderungen
        db.commit()
        
        return JSONResponse(content={
            "message": "Änderungen erfolgreich gespeichert",
            "created_assignments": created_assignments,
            "total_slots": len(request.assignments),
            "weeks": weeks,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
        })
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"[ERROR] Fehler beim Speichern der manuellen Änderungen: {e}")
        raise HTTPException(status_code=500, detail=f"Fehler beim Speichern: {str(e)}")
