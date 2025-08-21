#!/usr/bin/env python3
"""
Einfaches Script zur Verwaltung der Anwesenheitstage
"""
import sqlite3
import sys

def show_help():
    print("""
Anwesenheitstage-Verwaltung
===========================

Verwendung:
  python manage_attendance.py list                    - Zeigt alle Lehrkräfte
  python manage_attendance.py set <Nachname> <Tage>  - Setzt Anwesenheitstage
  
Beispiele:
  python manage_attendance.py set Tiemann Mo,Mi,Fr
  python manage_attendance.py set Schmidt Mo-Fr
  python manage_attendance.py set Meyer Di,Do

Tage: Mo, Di, Mi, Do, Fr oder Mo-Fr für alle Tage
""")

def list_teachers():
    conn = sqlite3.connect('vertretungsplan.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT id, last_name, first_name, attendance_days FROM teachers ORDER BY last_name')
    
    print(f"{'ID':3} {'Nachname':15} {'Vorname':15} {'Code':4} {'Anwesenheitstage':20}")
    print("-" * 60)
    
    for teacher_id, last_name, first_name, attendance_days in cursor.fetchall():
        days = []
        if attendance_days & 1: days.append('Mo')
        if attendance_days & 2: days.append('Di')
        if attendance_days & 4: days.append('Mi')
        if attendance_days & 8: days.append('Do')
        if attendance_days & 16: days.append('Fr')
        
        days_str = ', '.join(days) if days else 'Keine'
        print(f"{teacher_id:3} {last_name:15} {first_name:15} {attendance_days:4} {days_str:20}")
    
    conn.close()

def set_attendance(last_name, days_str):
    day_map = {'Mo': 1, 'Di': 2, 'Mi': 4, 'Do': 8, 'Fr': 16}
    
    if days_str == 'Mo-Fr':
        attendance_value = 31
    else:
        attendance_value = 0
        for day in days_str.split(','):
            day = day.strip()
            if day in day_map:
                attendance_value += day_map[day]
            else:
                print(f"Unbekannter Tag: {day}")
                return
    
    conn = sqlite3.connect('vertretungsplan.db')
    cursor = conn.cursor()
    
    cursor.execute('UPDATE teachers SET attendance_days = ? WHERE last_name = ?', 
                   (attendance_value, last_name))
    
    if cursor.rowcount == 0:
        print(f"Keine Lehrkraft mit Nachname '{last_name}' gefunden.")
    else:
        conn.commit()
        print(f"✅ Anwesenheitstage für {last_name} auf {days_str} gesetzt (Code: {attendance_value})")
    
    conn.close()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        show_help()
    elif sys.argv[1] == 'list':
        list_teachers()
    elif sys.argv[1] == 'set' and len(sys.argv) == 4:
        set_attendance(sys.argv[2], sys.argv[3])
    else:
        show_help()
