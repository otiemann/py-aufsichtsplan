"""GPU001.TXT Import Service

Liest Stundenplandaten aus GPU001.TXT und importiert Unterrichtsstunden
für Lehrkräfte in die Datenbank.

Format der GPU001.TXT:
- Spalte 3: Kürzel der Lehrkraft (Index 2)
- Spalte 6: Wochentag (Index 5) - 1=Montag, 2=Dienstag, ..., 5=Freitag
- Spalte 7: Unterrichtsstunde (Index 6) - 1-8

Beispiel:
4063;"12ZU4A";"HOO";"ENG";"3035";2;13;;
     ↑        ↑                     ↑ ↑
     ID       Kürzel               Tag Stunde
"""

from typing import List, Dict, Set
from sqlalchemy.orm import Session
from ..models import Teacher, TeacherLesson


def parse_gpu_line(line: str) -> tuple[str, int, int] | None:
    """Parst eine Zeile der GPU001.TXT
    
    Returns:
        (abbreviation, weekday, hour) oder None bei Fehlern
    """
    try:
        # Entferne Whitespace und splitte an Semikolon
        parts = line.strip().split(';')
        if len(parts) < 7:
            return None
        
        # Entferne Anführungszeichen
        abbreviation = parts[2].strip('"')
        weekday_str = parts[5].strip('"')
        hour_str = parts[6].strip('"')
        
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
            
        return abbreviation, weekday_zero_based, hour
        
    except (ValueError, IndexError):
        return None


def import_gpu_file(db: Session, file_path: str) -> Dict[str, int]:
    """Importiert Stundenplandaten aus GPU001.TXT
    
    Returns:
        Dict mit Statistiken: {"processed": int, "imported": int, "errors": int}
    """
    stats = {"processed": 0, "imported": 0, "errors": 0, "unknown_teachers": 0}
    
    # Hole alle Lehrkräfte mit ihren Kürzeln
    teachers_by_abbrev = {}
    for teacher in db.query(Teacher).filter(Teacher.abbreviation.isnot(None)).all():
        teachers_by_abbrev[teacher.abbreviation] = teacher
    
    # Sammle alle Unterrichtsstunden pro Lehrkraft
    lessons_by_teacher: Dict[int, Set[tuple[int, int]]] = {}  # teacher_id -> {(weekday, hour)}
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                stats["processed"] += 1
                
                result = parse_gpu_line(line)
                if not result:
                    stats["errors"] += 1
                    continue
                    
                abbreviation, weekday, hour = result
                
                # Finde Lehrkraft
                teacher = teachers_by_abbrev.get(abbreviation)
                if not teacher:
                    stats["unknown_teachers"] += 1
                    continue
                
                if teacher.id not in lessons_by_teacher:
                    lessons_by_teacher[teacher.id] = set()
                
                lessons_by_teacher[teacher.id].add((weekday, hour))
    
    except FileNotFoundError:
        raise FileNotFoundError(f"GPU001.TXT nicht gefunden: {file_path}")
    except Exception as e:
        raise Exception(f"Fehler beim Lesen der GPU001.TXT: {e}")
    
    # Lösche bestehende Lessons
    db.query(TeacherLesson).delete()
    db.commit()
    
    # Importiere neue Lessons
    for teacher_id, lessons in lessons_by_teacher.items():
        for weekday, hour in lessons:
            lesson = TeacherLesson(
                teacher_id=teacher_id,
                weekday=weekday,
                hour=hour
            )
            db.add(lesson)
            stats["imported"] += 1
    
    db.commit()
    
    # Aktualisiere Anwesenheitstage basierend auf Unterrichtsstunden
    _update_attendance_days_from_lessons(db)
    
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


def get_lesson_stats(db: Session) -> Dict[str, int]:
    """Gibt Statistiken über importierte Stunden zurück"""
    total_lessons = db.query(TeacherLesson).count()
    teachers_with_lessons = db.query(TeacherLesson.teacher_id).distinct().count()
    
    return {
        "total_lessons": total_lessons,
        "teachers_with_lessons": teachers_with_lessons,
        "teachers_total": db.query(Teacher).count()
    }
