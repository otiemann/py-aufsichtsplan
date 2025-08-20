from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.requests import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import csv
import io

from ..database import get_db
from ..models import Teacher, TeacherQuota, Floor

router = APIRouter()

# Templates aus main übernehmen, damit alle Router identische Loader nutzen
try:
    from ..main import templates as _main_templates
    templates = _main_templates  # type: ignore
except Exception:
    # Fallback (Dev)
    templates = Jinja2Templates(directory="app/templates")


@router.get("/")
async def admin_index(request: Request):
    return templates.TemplateResponse("admin/index.html", {"request": request})


@router.get("/teachers")
async def admin_teachers(request: Request, db: Session = Depends(get_db)):
    teachers = db.query(Teacher).order_by(Teacher.last_name, Teacher.first_name).all()
    floors = db.query(Floor).order_by(Floor.order_index, Floor.name).all()
    return templates.TemplateResponse("admin/teachers.html", {"request": request, "teachers": teachers, "floors": floors})


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
async def set_exempt(teacher_id: int = Form(...), value: int = Form(1), db: Session = Depends(get_db)):
    t = db.get(Teacher, teacher_id)
    if t is not None:
        t.exempt = bool(value)
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



