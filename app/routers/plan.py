from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional, List, Dict

from fastapi import APIRouter, Depends, Form
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.requests import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import and_, func

from ..database import get_db
from ..models import Floor, DutySlot, Assignment, Teacher
from ..services.scheduler import generate_assignments
from ..services.pdf_export import generate_pdf, generate_pdf_by_floor
from ..services.gpu009_export import generate_gpu009

router = APIRouter()

templates = Jinja2Templates(directory="app/templates")


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
    grid: List[List[List[str]]] = []
    for day_offset in range(5):
        d = start_date + timedelta(days=day_offset)
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
                    labels = [t.abbreviation or f"{t.last_name}, {t.first_name}" for t in teachers]
                cell_lines.append(f"{f.name}: {', '.join(labels) if labels else '—'}")
            row.append(cell_lines)
        grid.append(row)
    return grid


def week_counts(db: Session, start_date: date, end_date: date) -> List[Dict]:
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
    for t, cnt in rows:
        out.append({
            "abbreviation": t.abbreviation or "",
            "first_name": t.first_name,
            "last_name": t.last_name,
            "count": int(cnt or 0),
        })
    return out


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
    counts = week_counts(db, start_date, end_date)
    return templates.TemplateResponse(
        "plan/generate.html",
        {
            "request": request,
            "day_labels": weekday_labels(),
            "break_labels": break_labels(),
            "breaks_per_day": bpd,
            "grid": grid,
            "counts": counts,
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
    counts = week_counts(db, start_date, end_date)
    return templates.TemplateResponse(
        "plan/week.html",
        {
            "request": request,
            "day_labels": weekday_labels(),
            "break_labels": break_labels(),
            "breaks_per_day": bpd,
            "grid": grid,
            "counts": counts,
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
