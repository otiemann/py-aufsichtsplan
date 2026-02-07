from typing import List, Optional, Union
import os
import tempfile
import re

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.requests import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import csv
import io

from ..database import get_db, SQLALCHEMY_DATABASE_URL
from ..db_config import (
    clear_database_path_config,
    get_database_config_path,
    list_database_candidates,
    read_database_path_config,
    write_database_path_config,
)
from ..models import Teacher, TeacherQuota, Floor, TeacherLesson
from ..services.gpu_import import import_gpu_file, clear_lessons, get_lesson_stats, update_attendance_from_lessons

router = APIRouter()


WEEKDAY_ITEMS = [
    ("Mo", 0),
    ("Di", 1),
    ("Mi", 2),
    ("Do", 3),
    ("Fr", 4),
]


def _parse_hour_token(token: str) -> List[int]:
    """Parst einen einzelnen Stunden-Token (z.B. '3' oder '2-5')."""
    token = (token or "").strip()
    if not token:
        return []

    if "-" in token:
        parts = token.split("-", 1)
        if len(parts) != 2 or not parts[0].strip().isdigit() or not parts[1].strip().isdigit():
            raise ValueError(f"Ungültiger Bereich '{token}'. Erlaubt sind z.B. 2-5 oder 7.")
        start = int(parts[0].strip())
        end = int(parts[1].strip())
        if start > end:
            raise ValueError(f"Ungültiger Bereich '{token}': Start muss <= Ende sein.")
        if start < 1 or end > 20:
            raise ValueError(f"Ungültiger Bereich '{token}': Stunden müssen zwischen 1 und 20 liegen.")
        return list(range(start, end + 1))

    if not token.isdigit():
        raise ValueError(f"Ungültige Stunde '{token}'. Erlaubt sind Zahlen oder Bereiche (z.B. 3 oder 2-5).")

    value = int(token)
    if value < 1 or value > 20:
        raise ValueError(f"Ungültige Stunde '{token}': Stunden müssen zwischen 1 und 20 liegen.")
    return [value]


def parse_hours_input(raw_value: str) -> List[int]:
    """Parst Stunden-Eingabe je Tag (z.B. '1,2,5' oder '1-3 7')."""
    raw = (raw_value or "").strip()
    if not raw:
        return []

    tokens = [token for token in re.split(r"[,\s;]+", raw) if token]
    hours: set[int] = set()
    for token in tokens:
        for hour in _parse_hour_token(token):
            hours.add(hour)
    return sorted(hours)


def parse_checkbox_hours(values: List[str]) -> List[int]:
    """Parst Stunden aus Checkbox-Werten (mehrfaches Form-Feld)."""
    hours: set[int] = set()
    for raw in values:
        token = (raw or "").strip()
        if not token:
            continue
        if not token.isdigit():
            raise ValueError(f"Ungültige Stunde '{token}'.")
        hour = int(token)
        if hour < 1 or hour > 20:
            raise ValueError(f"Ungültige Stunde '{token}': Stunden müssen zwischen 1 und 20 liegen.")
        hours.add(hour)
    return sorted(hours)

# Templates aus main übernehmen, damit alle Router identische Loader nutzen
try:
    from ..main import templates as _main_templates
    templates = _main_templates  # type: ignore
except Exception:
    # Fallback (Dev)
    templates = Jinja2Templates(directory="app/templates")


def _current_sqlite_path() -> str:
    if SQLALCHEMY_DATABASE_URL.startswith("sqlite:///"):
        return os.path.abspath(SQLALCHEMY_DATABASE_URL[10:])
    return SQLALCHEMY_DATABASE_URL


@router.get("/")
async def admin_index(request: Request, db: Session = Depends(get_db)):
    lesson_stats = get_lesson_stats(db)
    current_db_path = _current_sqlite_path()
    configured_db_path = read_database_path_config()
    config_file_path = get_database_config_path()
    db_candidates = list_database_candidates()
    if current_db_path not in db_candidates:
        db_candidates = [current_db_path] + db_candidates

    db_path_source = "Standardpfad"
    if os.environ.get("DATABASE_PATH"):
        db_path_source = "Umgebungsvariable DATABASE_PATH"
    elif configured_db_path:
        db_path_source = "Gespeicherte Anwendungseinstellung"

    return templates.TemplateResponse("admin/index.html", {
        "request": request, 
        "lesson_stats": lesson_stats,
        "current_db_path": current_db_path,
        "configured_db_path": configured_db_path,
        "db_config_path": config_file_path,
        "db_candidates": db_candidates,
        "db_path_source": db_path_source,
    })


@router.get("/teachers")
async def admin_teachers(request: Request, db: Session = Depends(get_db)):
    teachers = db.query(Teacher).order_by(Teacher.last_name, Teacher.first_name).all()
    floors = db.query(Floor).order_by(Floor.order_index, Floor.name).all()
    lessons_editor_selected = {}
    for teacher in teachers:
        by_day = {}
        for day_label, day_index in WEEKDAY_ITEMS:
            day_hours = sorted({
                int(lesson.hour)
                for lesson in (teacher.lessons or [])
                if int(lesson.weekday) == day_index and 1 <= int(lesson.hour) <= 20
            })
            by_day[day_label] = day_hours
        lessons_editor_selected[int(teacher.id)] = by_day
    return templates.TemplateResponse(
        "admin/teachers.html",
        {
            "request": request,
            "teachers": teachers,
            "floors": floors,
            "lessons_editor_selected": lessons_editor_selected,
        },
    )


@router.post("/teachers/upload")
async def upload_teachers(
    request: Request,
    file: UploadFile = File(...),
    delimiter: Optional[str] = Form(","),
    db: Session = Depends(get_db),
):
    content = await file.read()
    text_stream = io.StringIO(content.decode("utf-8-sig"))

    delim = delimiter or ","

    created = 0
    updated = 0

    def upsert(first_name: Optional[str], last_name: Optional[str], abbreviation: Optional[str]):
        nonlocal created, updated
        first_name = (first_name or "").strip()
        last_name = (last_name or "").strip()
        abbreviation = (abbreviation or None)
        if not first_name or not last_name:
            return
        existing = None
        if abbreviation:
            existing = db.query(Teacher).filter(Teacher.abbreviation == abbreviation).one_or_none()
        if existing is None:
            existing = (
                db.query(Teacher)
                .filter(Teacher.first_name == first_name, Teacher.last_name == last_name)
                .one_or_none()
            )
        if existing:
            existing.first_name = first_name
            existing.last_name = last_name
            if abbreviation:
                existing.abbreviation = abbreviation
            updated += 1
        else:
            db.add(Teacher(first_name=first_name, last_name=last_name, abbreviation=abbreviation))
            created += 1

    # positionsbasiert (tableExport.csv): Nachname, Vorname, Kürzel, Fächer
    text_stream.seek(0)
    reader_pos = csv.reader(text_stream, delimiter=delim)
    rows = list(reader_pos)
    if rows:
        header = [(h or "").lstrip("\ufeff").strip() for h in rows[0]]
        third = header[2].lower() if len(header) >= 3 else ""
        if len(header) >= 3 and header[0].lower().startswith("nachname") and header[1].lower().startswith("vorname") and (third.startswith("kürzel") or third.startswith("kuerzel")):
            for r in rows[1:]:
                if not r or len(r) < 2:
                    continue
                last_name = (r[0] or "").strip()
                first_name = (r[1] or "").strip()
                abbreviation = (r[2] or "").strip() if len(r) >= 3 else None
                upsert(first_name, last_name, abbreviation or None)
            db.commit()
            response = RedirectResponse(url="/admin/teachers", status_code=303)
            response.set_cookie("flash", f"{created} neu, {updated} aktualisiert")
            return response

    # Fallback: heuristischer DictReader
    text_stream.seek(0)
    dict_reader = csv.DictReader(text_stream, delimiter=delim)
    if not dict_reader.fieldnames:
        response = RedirectResponse(url="/admin/teachers", status_code=303)
        response.set_cookie("flash", "Keine Headerzeile gefunden")
        return response

    normalized_headers = [(h or "").lstrip("\ufeff").strip() for h in dict_reader.fieldnames]

    def get_value(row: dict, prefixes: List[str]) -> Optional[str]:
        for h in normalized_headers:
            hl = h.lower()
            if any(hl.startswith(p) for p in prefixes):
                v = row.get(h)
                if v is None:
                    continue
                v = v.strip()
                if v != "":
                    return v
        return None

    rows2 = list(dict_reader)
    if not rows2:
        response = RedirectResponse(url="/admin/teachers", status_code=303)
        response.set_cookie("flash", "Keine Datenzeilen gefunden")
        return response

    for row in rows2:
        last_name = get_value(row, ["nachname", "name"])  # bevorzugt Nachname
        first_name = get_value(row, ["vorname"])  # bevorzugt Vorname
        abbreviation = get_value(row, ["kürzel", "kuerzel", "abk", "abbr"])  # Kürzel per Präfix
        upsert(first_name, last_name, abbreviation)

    db.commit()

    response = RedirectResponse(url="/admin/teachers", status_code=303)
    response.set_cookie("flash", f"{created} neu, {updated} aktualisiert")
    return response


@router.post("/teachers/set-exempt")
async def set_exempt(teacher_id: int = Form(...), exempt: bool = Form(False), db: Session = Depends(get_db)):
    t = db.get(Teacher, teacher_id)
    if t is not None:
        t.exempt = exempt
        db.commit()
    return RedirectResponse(url="/admin/teachers", status_code=303)


@router.post("/teachers/set-preferred-floor")
async def set_preferred_floor(teacher_id: int = Form(...), preferred_floor_id: Optional[int] = Form(None), db: Session = Depends(get_db)):
    t = db.get(Teacher, teacher_id)
    if t is not None:
        t.preferred_floor_id = preferred_floor_id or None
        db.commit()
    return RedirectResponse(url="/admin/teachers", status_code=303)


@router.post("/teachers/delete")
async def delete_teacher(teacher_id: int = Form(...), db: Session = Depends(get_db)):
    t = db.get(Teacher, teacher_id)
    if t is not None:
        db.delete(t)
        db.commit()
    return RedirectResponse(url="/admin/teachers", status_code=303)


@router.post("/teachers/bulk")
async def bulk_edit_teachers(
    action: str = Form(...),
    ids: List[int] = Form([]),
    db: Session = Depends(get_db),
):
    if not ids:
        response = RedirectResponse(url="/admin/teachers", status_code=303)
        response.set_cookie("flash", "Keine Lehrkräfte ausgewählt")
        return response

    affected = 0
    if action == "set_exempt":
        for t in db.query(Teacher).filter(Teacher.id.in_(ids)).all():
            if not t.exempt:
                t.exempt = True
                affected += 1
        db.commit()
        msg = f"{affected} als befreit markiert"
    elif action == "unset_exempt":
        for t in db.query(Teacher).filter(Teacher.id.in_(ids)).all():
            if t.exempt:
                t.exempt = False
                affected += 1
        db.commit()
        msg = f"Befreiung bei {affected} entfernt"
    else:
        msg = "Unbekannte Aktion"

    response = RedirectResponse(url="/admin/teachers", status_code=303)
    response.set_cookie("flash", msg)
    return response


@router.post("/teachers/set-quota")
async def set_quota(teacher_id: int = Form(...), target_duties: int = Form(0), db: Session = Depends(get_db)):
    t = db.get(Teacher, teacher_id)
    if t:
        if t.quota:
            t.quota.target_duties = max(0, target_duties or 0)
        else:
            db.add(TeacherQuota(teacher_id=t.id, target_duties=max(0, target_duties or 0)))
        db.commit()
    return RedirectResponse(url="/admin/teachers", status_code=303)


@router.post("/teachers/bulk-quota")
async def bulk_quota(target_duties: int = Form(...), ids: List[int] = Form([]), db: Session = Depends(get_db)):
    if not ids:
        response = RedirectResponse(url="/admin/teachers", status_code=303)
        response.set_cookie("flash", "Keine Lehrkräfte ausgewählt")
        return response
    value = max(0, target_duties or 0)
    affected = 0
    for t in db.query(Teacher).filter(Teacher.id.in_(ids)).all():
        if t.quota:
            if t.quota.target_duties != value:
                t.quota.target_duties = value
                affected += 1
        else:
            db.add(TeacherQuota(teacher_id=t.id, target_duties=value))
            affected += 1
    db.commit()
    response = RedirectResponse(url="/admin/teachers", status_code=303)
    response.set_cookie("flash", f"Soll-Aufsichten bei {affected} gesetzt")
    return response


@router.post("/teachers/set-attendance")
async def set_attendance(
    request: Request,
    teacher_id: int = Form(...), 
    db: Session = Depends(get_db)
):
    """Setzt die Anwesenheitstage (Wochentage) für eine Lehrkraft"""
    teacher = db.query(Teacher).filter(Teacher.id == teacher_id).first()
    if not teacher:
        response = RedirectResponse(url="/admin/teachers", status_code=303)
        response.set_cookie("flash", "Lehrkraft nicht gefunden", max_age=5)
        return response
    
    # Hole alle attendance_days aus dem Raw-Form-Data
    form_data = await request.form()
    attendance_days = form_data.getlist("attendance_days")
    
    teacher.set_attendance_days(attendance_days)
    db.commit()
    
    days_text = ", ".join(attendance_days) if attendance_days else "Keine Tage"
    response = RedirectResponse(url="/admin/teachers", status_code=303)
    response.set_cookie("flash", f"Anwesenheitstage für {teacher.last_name} gesetzt: {days_text}", max_age=5)
    return response


@router.post("/teachers/bulk-attendance")
async def bulk_set_attendance(
    request: Request,
    db: Session = Depends(get_db)
):
    """Setzt Anwesenheitstage für mehrere Lehrkräfte gleichzeitig"""
    # Hole alle Werte aus dem Raw-Form-Data
    form_data = await request.form()
    ids = [int(id_str) for id_str in form_data.getlist("ids")]
    bulk_days = form_data.getlist("bulk_days")
    
    if not ids:
        response = RedirectResponse(url="/admin/teachers", status_code=303)
        response.set_cookie("flash", "Keine Lehrkräfte ausgewählt", max_age=5)
        return response
    
    teachers = db.query(Teacher).filter(Teacher.id.in_(ids)).all()
    
    for teacher in teachers:
        teacher.set_attendance_days(bulk_days)
    
    db.commit()
    
    days_text = ", ".join(bulk_days) if bulk_days else "Keine Tage"
    response = RedirectResponse(url="/admin/teachers", status_code=303)
    response.set_cookie("flash", f"Anwesenheitstage für {len(teachers)} Lehrkräfte gesetzt: {days_text}", max_age=5)
    return response


@router.post("/teachers/set-lessons")
async def set_teacher_lessons(
    request: Request,
    teacher_id: int = Form(...),
    lesson_mo: str = Form(""),
    lesson_di: str = Form(""),
    lesson_mi: str = Form(""),
    lesson_do: str = Form(""),
    lesson_fr: str = Form(""),
    db: Session = Depends(get_db),
):
    """Setzt den Stundenplan (Mo-Fr, Stunde 1-20) für eine Lehrkraft."""
    teacher = db.query(Teacher).filter(Teacher.id == teacher_id).first()
    if not teacher:
        response = RedirectResponse(url="/admin/teachers", status_code=303)
        response.set_cookie("flash", "Lehrkraft nicht gefunden", max_age=5)
        return response

    day_inputs = [
        ("Mo", 0, "mo", lesson_mo),
        ("Di", 1, "di", lesson_di),
        ("Mi", 2, "mi", lesson_mi),
        ("Do", 3, "do", lesson_do),
        ("Fr", 4, "fr", lesson_fr),
    ]

    form_data = await request.form()

    parsed_by_day: dict[int, List[int]] = {}
    errors: List[str] = []
    for day_label, day_index, day_key, raw_fallback in day_inputs:
        try:
            checkbox_values = form_data.getlist(f"lesson_{day_key}_hours")
            if checkbox_values:
                parsed_by_day[day_index] = parse_checkbox_hours(checkbox_values)
            else:
                # Fallback kompatibel zum bisherigen Textfeld-Format.
                parsed_by_day[day_index] = parse_hours_input(raw_fallback)
        except ValueError as exc:
            errors.append(f"{day_label}: {exc}")

    if errors:
        response = RedirectResponse(url="/admin/teachers", status_code=303)
        response.set_cookie("flash", "Stundenplan nicht gespeichert. " + " | ".join(errors[:3]), max_age=8)
        return response

    # Ersetze alle vorhandenen Lessons der Lehrkraft.
    db.query(TeacherLesson).filter(TeacherLesson.teacher_id == teacher.id).delete(synchronize_session=False)

    total_lessons = 0
    for day_index, hours in parsed_by_day.items():
        for hour in hours:
            db.add(TeacherLesson(teacher_id=teacher.id, weekday=day_index, hour=hour))
            total_lessons += 1

    # Anwesenheit künftig aus Stundenplan ableiten.
    teacher.attendance_days = 31
    db.commit()

    response = RedirectResponse(url="/admin/teachers", status_code=303)
    response.set_cookie(
        "flash",
        f"Stundenplan für {teacher.last_name} gespeichert ({total_lessons} Stunden). Anwesenheit wird aus Stundenplan abgeleitet.",
        max_age=8,
    )
    return response


@router.post("/import-gpu")
async def import_gpu(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Importiert Stundenplandaten aus GPU001.TXT"""
    filename = file.filename or ""
    if not filename or os.path.splitext(filename)[1].lower() != ".txt":
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie("flash", "Bitte eine .TXT-Datei auswählen", max_age=5)
        return response
    
    temp_path = None
    try:
        # Temporäre Datei speichern (plattformunabhängig)
        content = await file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp_file:
            tmp_file.write(content)
            temp_path = tmp_file.name

        # Importieren
        stats = import_gpu_file(db, temp_path)
        
        response = RedirectResponse(url="/admin", status_code=303)
        flash_msg = f"GPU-Import erfolgreich: {stats['imported']} Stunden importiert, Anwesenheitstage automatisch aktualisiert"
        if stats['unknown_teachers'] > 0:
            flash_msg += f", {stats['unknown_teachers']} unbekannte Lehrkräfte ignoriert"
        
        response.set_cookie("flash", flash_msg, max_age=10)
        return response
        
    except Exception as e:
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie("flash", f"Fehler beim GPU-Import: {str(e)}", max_age=10)
        return response
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


@router.post("/clear-lessons")
async def clear_lessons_route(db: Session = Depends(get_db)):
    """Löscht alle Unterrichtsstunden"""
    count = clear_lessons(db)
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie("flash", f"{count} Unterrichtsstunden gelöscht", max_age=5)
    return response


@router.post("/update-attendance-from-lessons")
async def update_attendance_from_lessons_route(db: Session = Depends(get_db)):
    """Aktualisiert Anwesenheitstage aller Lehrkräfte basierend auf ihren Unterrichtsstunden"""
    count = update_attendance_from_lessons(db)
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie("flash", f"Anwesenheitstage von {count} Lehrkräften aus Stundenplan aktualisiert", max_age=5)
    return response


@router.post("/database/select")
async def select_database_path(
    database_path: str = Form(...),
):
    try:
        normalized = write_database_path_config(database_path)
    except ValueError as exc:
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie("flash", f"Datenbankpfad ungültig: {exc}", max_age=8)
        return response
    except OSError as exc:
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie("flash", f"Datenbankpfad konnte nicht gespeichert werden: {exc}", max_age=8)
        return response

    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        "flash",
        f"Datenbankpfad gespeichert: {normalized}. Bitte Anwendung neu starten.",
        max_age=12,
    )
    return response


@router.post("/database/reset")
async def reset_database_path():
    clear_database_path_config()
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        "flash",
        "Datenbankpfad-Einstellung zurückgesetzt. Bitte Anwendung neu starten.",
        max_age=12,
    )
    return response
