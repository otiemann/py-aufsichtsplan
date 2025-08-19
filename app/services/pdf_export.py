from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO
from typing import List

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from sqlalchemy.orm import Session

from ..models import DutySlot, Assignment, Floor, Teacher


def weekday_labels() -> List[str]:
    return ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]


def break_labels() -> List[str]:
    return ["0. Stunde", "2./3. Stunde", "4./5. Stunde", "6./7. Stunde"]


def daterange(start: date, end: date) -> List[date]:
    days = []
    d = start
    while d <= end:
        days.append(d)
        d = d + timedelta(days=1)
    return days


def build_cell_floor_table(styles, lines: List[str]) -> Table:
    # Verschachtelte Tabelle pro Zelle: jede Zeile = ein Stockwerk mit Rahmen
    data = [[Paragraph(line.replace("&", "&amp;"), styles["Normal"])] for line in lines]
    if not data:
        data = [[Paragraph("—", styles["Normal"])]]
    t = Table(data)
    t.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.25, colors.grey),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 2),
        ("RIGHTPADDING", (0,0), (-1,-1), 2),
        ("TOPPADDING", (0,0), (-1,-1), 1),
        ("BOTTOMPADDING", (0,0), (-1,-1), 1),
    ]))
    return t


def build_week_grid_data(db: Session, start_date: date, breaks_per_day: int) -> List[List[List[str]]]:
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


def generate_pdf(db: Session, start_date: date, end_date: date) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=24, leftMargin=24, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()

    story: List = []
    title = "Pausenaufsicht (Woche Mo–Fr)"
    story.append(Paragraph(title, styles["Title"]))
    story.append(Spacer(1, 12))

    bpd = 4
    grid = build_week_grid_data(db, start_date, bpd)

    headers = ["Tag"] + break_labels()
    data: List[List] = [headers]

    days = weekday_labels()
    for d_idx in range(5):
        row: List = [days[d_idx]]
        for b in range(bpd):
            lines = grid[d_idx][b]
            row.append(build_cell_floor_table(styles, lines))
        data.append(row)

    # Spaltenbreiten: erste Spalte schmal, Rest gleich verteilt
    total_width = A4[0] - (doc.leftMargin + doc.rightMargin)
    first_col = 70
    other_col = (total_width - first_col) / 4

    table = Table(data, repeatRows=1, colWidths=[first_col] + [other_col]*4)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("GRID", (0,0), (-1,-1), 0.5, colors.black),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.lightyellow]),
    ]))

    story.append(table)

    doc.build(story)
    return buffer.getvalue()


def generate_pdf_by_floor(db: Session, start_date: date, end_date: date) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=24, leftMargin=24, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()

    story: List = []
    story.append(Paragraph("Pausenaufsicht nach Stockwerken", styles["Title"]))
    story.append(Spacer(1, 12))

    bpd = 4
    floors = db.query(Floor).order_by(Floor.order_index, Floor.name).all()

    for idx, f in enumerate(floors):
        story.append(Paragraph(f.name, styles["Heading2"]))
        headers = ["Tag"] + break_labels()
        data: List[List] = [headers]

        for day_offset, day_label in enumerate(weekday_labels()):
            d = start_date + timedelta(days=day_offset)
            row: List = [day_label]
            for b in range(1, bpd + 1):
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
                cell = Paragraph(", ".join(labels) if labels else "—", styles["Normal"])
                row.append(cell)
            data.append(row)

        total_width = A4[0] - (doc.leftMargin + doc.rightMargin)
        first_col = 70
        other_col = (total_width - first_col) / 4
        t = Table(data, repeatRows=1, colWidths=[first_col] + [other_col]*4)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
            ("GRID", (0,0), (-1,-1), 0.5, colors.black),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.whitesmoke, colors.lightyellow]),
        ]))
        story.append(t)
        if idx != len(floors) - 1:
            story.append(Spacer(1, 12))

    doc.build(story)
    return buffer.getvalue()
