"""GPU001.TXT Import Service

Liest Stundenplandaten aus GPU001.TXT und importiert Unterrichtsstunden
für Lehrkräfte in die Datenbank.

Format der GPU001.TXT:
- Spalte 3: Kürzel der Lehrkraft (Index 2)
- Spalte 5: Raum (Index 4)
- Spalte 6: Wochentag (Index 5) - 1=Montag, 2=Dienstag, ..., 5=Freitag
- Spalte 7: Unterrichtsstunde (Index 6) - 1-8

Beispiel:
4063;"12ZU4A";"HOO";"ENG";"3035";2;13;;
     ↑        ↑                     ↑ ↑
     ID       Kürzel               Tag Stunde
"""

import csv
from typing import Any, Dict, Set

from sqlalchemy import func
from sqlalchemy.orm import Session
from ..models import Teacher, TeacherLesson


def parse_gpu_line(line: str) -> tuple[str, int, int, str | None] | None:
    """Parst eine Zeile der GPU001.TXT
    
    Returns:
        (abbreviation, weekday, hour, room) oder None bei Fehlern
    """
    try:
        if not line.strip():
            return None

        parts = next(csv.reader([line], delimiter=";", quotechar='"'))
        if len(parts) < 7:
            return None
        
        abbreviation = parts[2].strip()
        room = parts[4].strip() or None
        weekday_str = parts[5].strip()
        hour_str = parts[6].strip()
        
        if not abbreviation or not weekday_str or not hour_str:
            return None
            
        weekday = int(weekday_str)
        hour = int(hour_str)
        
        # Validierung
        if weekday < 1 or weekday > 5:  # 1-5 für Mo-Fr
            return None
        if hour < 1 or hour > 20:  # Erweiterte Stunden bis 20 (für Nachmittag/Abend)
            return None
            
        # Konvertiere zu 0-basiert (0=Montag)
        weekday_zero_based = weekday - 1
            
        return abbreviation, weekday_zero_based, hour, room
        
    except (ValueError, IndexError, csv.Error, StopIteration):
        return None


def import_gpu_file(db: Session, file_path: str) -> Dict[str, Any]:
    """Importiert Stundenplandaten aus GPU001.TXT
    
    Returns:
        Dict mit Statistiken: {"processed": int, "imported": int, "errors": int}
    """
    stats: Dict[str, Any] = {
        "processed": 0,
        "imported": 0,
        "errors": 0,
        "unknown_teachers": 0,
        "unknown_teacher_examples": [],
        "error_line_examples": [],
    }
    
    # Hole alle Lehrkräfte mit ihren Kürzeln
    teachers_by_abbrev = {}
    for teacher in db.query(Teacher).filter(Teacher.abbreviation.isnot(None)).all():
        teachers_by_abbrev[teacher.abbreviation] = teacher
    
    # Sammle alle Unterrichtsstunden pro Lehrkraft
    lessons_by_teacher: Dict[int, Set[tuple[int, int, str | None]]] = {}  # teacher_id -> {(weekday, hour, room)}
    
    try:
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            for line_num, line in enumerate(f, 1):
                stats["processed"] += 1
                
                result = parse_gpu_line(line)
                if not result:
                    stats["errors"] += 1
                    if len(stats["error_line_examples"]) < 5:
                        stats["error_line_examples"].append(line_num)
                    continue
                    
                abbreviation, weekday, hour, room = result
                
                # Finde Lehrkraft
                teacher = teachers_by_abbrev.get(abbreviation)
                if not teacher:
                    stats["unknown_teachers"] += 1
                    if abbreviation not in stats["unknown_teacher_examples"] and len(stats["unknown_teacher_examples"]) < 8:
                        stats["unknown_teacher_examples"].append(abbreviation)
                    continue
                
                if teacher.id not in lessons_by_teacher:
                    lessons_by_teacher[teacher.id] = set()
                
                lessons_by_teacher[teacher.id].add((weekday, hour, room))
    
    except FileNotFoundError:
        raise FileNotFoundError(f"GPU001.TXT nicht gefunden: {file_path}")
    except Exception as e:
        raise Exception(f"Fehler beim Lesen der GPU001.TXT: {e}")

    try:
        # Lösche bestehende Lessons erst, wenn die Datei vollständig gelesen wurde.
        db.query(TeacherLesson).delete()

        # Importiere neue Lessons
        for teacher_id, lessons in lessons_by_teacher.items():
            for weekday, hour, room in lessons:
                lesson = TeacherLesson(
                    teacher_id=teacher_id,
                    weekday=weekday,
                    hour=hour,
                    room=room,
                )
                db.add(lesson)
                stats["imported"] += 1

        db.commit()

        # Aktualisiere Anwesenheitstage basierend auf Unterrichtsstunden
        _update_attendance_days_from_lessons(db)
    except Exception:
        db.rollback()
        raise
    
    return stats


def clear_lessons(db: Session) -> int:
    """Löscht alle Unterrichtsstunden
    
    Returns:
        Anzahl gelöschter Datensätze
    """
    count = db.query(TeacherLesson).count()
    db.query(TeacherLesson).delete()
    db.commit()
    return count


def _update_attendance_days_from_lessons(db: Session) -> None:
    """Aktualisiert Anwesenheitstage aller Lehrkräfte basierend auf ihren Unterrichtsstunden"""
    
    # Hole alle Lehrkräfte mit ihren Unterrichtsstunden
    teachers = db.query(Teacher).all()
    
    for teacher in teachers:
        # Sammle alle Wochentage, an denen die Lehrkraft Unterricht hat
        lesson_weekdays = set()
        for lesson in teacher.lessons:
            lesson_weekdays.add(lesson.weekday)
        
        if lesson_weekdays:
            # Berechne Bitflags für Anwesenheitstage
            attendance_bits = 0
            for weekday in lesson_weekdays:
                if 0 <= weekday <= 4:  # 0=Montag bis 4=Freitag
                    attendance_bits |= (1 << weekday)
            
            # Setze Anwesenheitstage nur, wenn sie aktuell leer sind (None oder 0)
            # oder wenn alle Tage gesetzt waren (31 = Mo-Fr, wahrscheinlich Default)
            if teacher.attendance_days is None or teacher.attendance_days == 0 or teacher.attendance_days == 31:
                teacher.attendance_days = attendance_bits
        
        # Falls keine Unterrichtsstunden vorhanden, behalte aktuelle Einstellung bei
    
    db.commit()


def update_attendance_from_lessons(db: Session) -> int:
    """Öffentliche Funktion zum manuellen Update der Anwesenheitstage
    
    Returns:
        Anzahl der aktualisierten Lehrkräfte
    """
    teachers_updated = 0
    teachers = db.query(Teacher).all()
    
    for teacher in teachers:
        old_attendance = teacher.attendance_days
        
        # Sammle alle Wochentage, an denen die Lehrkraft Unterricht hat
        lesson_weekdays = set()
        for lesson in teacher.lessons:
            lesson_weekdays.add(lesson.weekday)
        
        if lesson_weekdays:
            # Berechne Bitflags für Anwesenheitstage
            attendance_bits = 0
            for weekday in lesson_weekdays:
                if 0 <= weekday <= 4:  # 0=Montag bis 4=Freitag
                    attendance_bits |= (1 << weekday)
            
            teacher.attendance_days = attendance_bits
            if old_attendance != attendance_bits:
                teachers_updated += 1
        elif teacher.lessons:  # Hat Lessons aber keine gültigen Wochentage
            # Setze auf "keine Anwesenheit" wenn nur ungültige Daten vorliegen
            if old_attendance != 0:
                teacher.attendance_days = 0
                teachers_updated += 1
    
    db.commit()
    return teachers_updated


def get_lesson_stats(db: Session) -> Dict[str, Any]:
    """Gibt Statistiken über importierte Stunden zurück"""
    total_lessons = db.query(TeacherLesson).count()
    teachers_with_lessons = db.query(TeacherLesson.teacher_id).distinct().count()
    day_labels = ["Mo", "Di", "Mi", "Do", "Fr"]
    lessons_by_day: Dict[str, int] = {label: 0 for label in day_labels}
    for weekday, count in (
        db.query(TeacherLesson.weekday, func.count(TeacherLesson.id))
        .group_by(TeacherLesson.weekday)
        .all()
    ):
        if weekday is not None and 0 <= int(weekday) < len(day_labels):
            lessons_by_day[day_labels[int(weekday)]] = int(count)
    
    return {
        "total_lessons": total_lessons,
        "teachers_with_lessons": teachers_with_lessons,
        "teachers_total": db.query(Teacher).count(),
        "unknown_teachers": 0,
        "lessons_by_day": lessons_by_day,
    }
