from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Tuple, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
import random

from ..models import Teacher, TeacherQuota, Floor, DutySlot, Assignment


def daterange(start: date, end: date) -> List[date]:
    current = start
    out: List[date] = []
    while current <= end:
        out.append(current)
        current += timedelta(days=1)
    return out


def is_weekday(d: date) -> bool:
    # Monday=0 .. Sunday=6
    return d.weekday() < 5


def ensure_slots(
    db: Session,
    start_date: date,
    end_date: date,
    breaks_per_day: int,
) -> List[DutySlot]:
    floors: List[Floor] = db.query(Floor).order_by(Floor.id).all()
    slots: List[DutySlot] = []

    for d in daterange(start_date, end_date):
        if not is_weekday(d):
            continue
        for b in range(1, breaks_per_day + 1):
            for f in floors:
                slot = (
                    db.query(DutySlot)
                    .filter(DutySlot.date == d, DutySlot.break_index == b, DutySlot.floor_id == f.id)
                    .one_or_none()
                )
                if slot is None:
                    slot = DutySlot(date=d, break_index=b, floor_id=f.id)
                    db.add(slot)
                    db.flush()
                slots.append(slot)
    db.commit()
    return slots


def clear_assignments(db: Session, start_date: date, end_date: date) -> None:
    slot_ids = [
        s_id
        for (s_id,) in db.query(DutySlot.id)
        .filter(DutySlot.date >= start_date, DutySlot.date <= end_date)
        .all()
    ]
    if slot_ids:
        db.query(Assignment).filter(Assignment.duty_slot_id.in_(slot_ids)).delete(synchronize_session=False)
        db.commit()


def generate_assignments(
    db: Session,
    start_date: date,
    end_date: date,
    breaks_per_day: int,
) -> None:
    slots = ensure_slots(db, start_date, end_date, breaks_per_day)
    clear_assignments(db, start_date, end_date)

    teachers = (
        db.query(Teacher)
        .join(TeacherQuota, isouter=True)
        .filter(Teacher.exempt == False)
        .all()
    )

    eligible: List[Tuple[Teacher, int]] = []
    teacher_to_target: Dict[int, int] = {}

    for t in teachers:
        target = t.quota.target_duties if t.quota else 0
        if target and target > 0:
            teacher_to_target[t.id] = target
            eligible.append((t, target))

    if not eligible:
        print("[DEBUG] Keine geeigneten Lehrkräfte für Scheduler gefunden!")
        return
    
    # Debug: Zeige alle Lehrkräfte mit ihren Zielen
    print(f"[DEBUG] {len(eligible)} Lehrkräfte für Scheduler:")
    for t, target in eligible[:10]:  # Zeige erste 10
        attendance_days = t.get_actual_attendance_days_display()
        print(f"  - {t.abbreviation}: Ziel {target}, Anwesend: {attendance_days}")
    if len(eligible) > 10:
        print(f"  ... und {len(eligible) - 10} weitere")
    
    # Debug: Zeige auch Lehrkräfte OHNE Ziele
    excluded_teachers = [t for t in teachers if not (t.quota and t.quota.target_duties > 0)]
    if excluded_teachers:
        print(f"[DEBUG] {len(excluded_teachers)} Lehrkräfte OHNE Soll-Aufsichten (nicht berücksichtigt):")
        for t in excluded_teachers[:10]:
            quota_val = t.quota.target_duties if t.quota else "Keine Quota"
            print(f"  - {t.abbreviation}: {quota_val}")
        if len(excluded_teachers) > 10:
            print(f"  ... und {len(excluded_teachers) - 10} weitere")

    # zufälliger Bias pro Lehrkraft für diese Planungsrunde
    random_bias: Dict[int, float] = {t.id: random.random() for t, _ in eligible}

    existing_counts: Dict[int, int] = {t.id: 0 for t, _ in eligible}

    q = (
        db.query(Assignment.teacher_id, func.count(Assignment.id))
        .join(DutySlot, DutySlot.id == Assignment.duty_slot_id)
        .filter(DutySlot.date >= start_date, DutySlot.date <= end_date)
        .group_by(Assignment.teacher_id)
        .all()
    )
    for teacher_id, cnt in q:
        if teacher_id in existing_counts:
            existing_counts[teacher_id] = cnt

    def pick_teacher(d: date, break_index: int, floor_id: int) -> Optional[Teacher]:
        # Berechne Wochentag (0=Montag, 4=Freitag)
        weekday = d.weekday()
        
        already = set(
            t_id
            for (t_id,) in db.query(Assignment.teacher_id)
            .join(DutySlot, DutySlot.id == Assignment.duty_slot_id)
            .filter(DutySlot.date == d, DutySlot.break_index == break_index)
            .all()
        )

        # Debug: Protokolliere warum Lehrkräfte ausgeschlossen werden
        debug_excluded = []
        
        # ALLE verfügbaren Kandidaten sammeln (keine Trennung nach Präferenz)
        all_candidates: List[Tuple[Teacher, int]] = []
        for t, target in eligible:
            if t.id in already:
                debug_excluded.append(f"{t.abbreviation}: bereits eingeteilt")
                continue
            
            # Prüfe Anwesenheit an diesem Wochentag
            if not t.is_available_on_weekday(weekday):
                debug_excluded.append(f"{t.abbreviation}: nicht anwesend {['Mo','Di','Mi','Do','Fr'][weekday]}")
                continue
            
            # Prüfe ob verfügbar für Pausenaufsicht (ohne Anwesenheitsprüfung)
            if not t.is_available_for_supervision(weekday, break_index):
                # Detaillierte Analyse warum nicht verfügbar
                relevant_hours = []
                if break_index == 1:
                    relevant_hours = [1]
                elif break_index == 2:
                    relevant_hours = [2, 3]
                elif break_index == 3:
                    relevant_hours = [4, 5]
                elif break_index == 4:
                    relevant_hours = [6, 7]
                
                debug_excluded.append(f"{t.abbreviation}: kein Unterricht in Stunden {relevant_hours}")
                continue
                
            assigned = existing_counts.get(t.id, 0)
            if assigned >= target:
                debug_excluded.append(f"{t.abbreviation}: Ziel erreicht ({assigned}/{target})")
                continue
            
            all_candidates.append((t, target))
        
        # Debug-Ausgabe nur wenn keine Kandidaten gefunden
        if not all_candidates and debug_excluded:
            print(f"[DEBUG] Keine Kandidaten für {d} Pause {break_index} Stockwerk {floor_id}:")
            for reason in debug_excluded[:5]:  # Zeige nur erste 5
                print(f"  - {reason}")
            if len(debug_excluded) > 5:
                print(f"  ... und {len(debug_excluded) - 5} weitere")
            
            # Bei den ersten 3 problematischen Slots: Zeige detaillierte Stundenplan-Info
            if len([r for r in debug_excluded if "kein Unterricht" in r]) >= 3:
                sample_teacher = next((t for t, _ in eligible if any(f"{t.abbreviation}:" in r for r in debug_excluded)), None)
                if sample_teacher:
                    day_name = ['Mo','Di','Mi','Do','Fr'][weekday]
                    lessons_today = [lesson.hour for lesson in sample_teacher.lessons if lesson.weekday == weekday]
                    relevant_hours = []
                    if break_index == 1:
                        relevant_hours = [1]
                    elif break_index == 2:
                        relevant_hours = [2, 3]
                    elif break_index == 3:
                        relevant_hours = [4, 5]
                    elif break_index == 4:
                        relevant_hours = [6, 7]
                    print(f"  [INFO] Beispiel {sample_teacher.abbreviation} {day_name}: Hat Stunden {sorted(lessons_today)}, braucht Stunden {relevant_hours}")

        if not all_candidates:
            return None

        # Erstelle zwei separate Pools: ohne und mit Aufsichten heute
        candidates_no_duties_today = []
        candidates_with_duties_today = []
        
        for t, target in all_candidates:
            assigned = existing_counts.get(t.id, 0)
            
            # Hole alle Pausen dieser Lehrkraft an diesem Tag
            existing_breaks = [
                break_idx for (break_idx,) in 
                db.query(DutySlot.break_index)
                .join(Assignment, Assignment.duty_slot_id == DutySlot.id)
                .filter(Assignment.teacher_id == t.id, DutySlot.date == d)
                .all()
            ]
            
            # AUSSCHLUSSKRITERIUM: Keine aufeinanderfolgenden Pausen erlauben
            has_consecutive = False
            for existing_break in existing_breaks:
                if abs(existing_break - break_index) == 1:
                    has_consecutive = True
                    break
            
            if has_consecutive:
                continue  # Diese Lehrkraft komplett ausschließen
            
            # AUSSCHLUSSKRITERIUM: Maximal 2 Aufsichten pro Tag
            duties_today = len(existing_breaks)
            if duties_today >= 2:
                continue  # Diese Lehrkraft komplett ausschließen
            
            is_preferred_floor = (t.preferred_floor_id == floor_id)

            # Trenne nach Aufsichten heute
            if duties_today == 0:
                candidates_no_duties_today.append((t, target, assigned, duties_today, is_preferred_floor, existing_breaks))
            else:
                candidates_with_duties_today.append((t, target, assigned, duties_today, is_preferred_floor, existing_breaks))
        
        candidate_entries = candidates_no_duties_today + candidates_with_duties_today
        zero_day_entries = [entry for entry in candidate_entries if entry[3] == 0]
        if zero_day_entries:
            candidate_entries = zero_day_entries
            priority_matrix = [
                (0, 0),  # Präferenz, keine Aufsicht heute
                (1, 0),  # Keine Präferenz, keine Aufsicht heute
                (2, 0),  # Konfliktpräferenz, keine Aufsicht heute
            ]
        else:
            priority_matrix = [
                (0, 0),  # Präferenz, keine Aufsicht heute
                (0, 1),  # Präferenz, bereits Aufsicht heute
                (1, 0),  # Keine Präferenz, keine Aufsicht heute
                (1, 1),  # Keine Präferenz, bereits Aufsicht heute
                (2, 0),  # Konfliktpräferenz, keine Aufsicht heute
                (2, 1),  # Konfliktpräferenz, bereits Aufsicht heute
            ]
        if candidate_entries:

            def classify(entry: Tuple[Teacher, int, int, int, bool]) -> Tuple[int, int]:
                t = entry[0]
                duties_today = entry[3]
                if t.preferred_floor_id == floor_id:
                    pref_group = 0
                elif t.preferred_floor_id is None:
                    pref_group = 1
                else:
                    pref_group = 2
                duty_group = 0 if duties_today == 0 else 1
                return pref_group, duty_group

            for pref_group, duty_group in priority_matrix:
                candidate_group = [
                    entry
                    for entry in candidate_entries
                    if classify(entry) == (pref_group, duty_group)
                ]
                if not candidate_group:
                    continue

                best_t = None
                best_key = None
                
                for entry in candidate_group:
                    t, target, assigned, duties_today, _is_preferred, existing_breaks = entry
                    # Basis-Bewertung
                    ratio = assigned / max(target, 1)
                    
                    # Sehr hohe Strafe für mehrere Aufsichten am gleichen Tag
                    same_day_penalty = duties_today * 5.0  # Drastisch erhöht
                    
                    # Berechne wie viele Tage diese Lehrkraft bereits Aufsichten hat
                    days_with_duties = (
                        db.query(DutySlot.date)
                        .join(Assignment, Assignment.duty_slot_id == DutySlot.id)
                        .filter(Assignment.teacher_id == t.id)
                        .filter(DutySlot.date >= start_date, DutySlot.date <= end_date)
                        .distinct()
                        .count()
                    )
                    
                    # Berechne verfügbare Tage für diese Lehrkraft
                    available_days = sum(1 for i in range(5) if t.is_available_on_weekday(i))
                    
                    # NEUE LOGIK: Starke Bevorzugung für Teilzeit-Lehrkräfte an ihren Anwesenheitstagen
                    # Je weniger Tage verfügbar, desto höher die Priorität an diesen Tagen
                    if available_days <= 2:  # 1-2 Tage: Sehr hohe Priorität
                        part_time_bonus = -2.0
                    elif available_days <= 3:  # 3 Tage: Hohe Priorität
                        part_time_bonus = -1.0
                    elif available_days <= 4:  # 4 Tage: Mittlere Priorität
                        part_time_bonus = -0.5
                    else:  # 5 Tage (Vollzeit): Normale Priorität
                        part_time_bonus = 0.0
                    
                    # Tagesverteilung: Wie gut sind die Aufsichten über die verfügbaren Tage verteilt?
                    day_distribution_factor = days_with_duties / max(available_days, 1)
                    
                    # Stockwerk-Präferenz als Bonus/Malus (verstärkt)
                    has_floor_preference = (t.preferred_floor_id == floor_id)
                    floor_preference_bonus = -0.8 if has_floor_preference else 0.0
                    
                    # Kombiniere alle Faktoren
                    combined_distribution_score = (
                        day_distribution_factor
                        + floor_preference_bonus
                        + part_time_bonus
                    )

                    # Anzahl der verbleibenden Optionen für spätere Pausen an diesem Tag (gleicher Standort)
                    future_options = 0
                    for future_break in range(break_index + 1, breaks_per_day + 1):
                        if any(abs(eb - future_break) == 1 for eb in existing_breaks):
                            continue
                        if not t.is_available_for_supervision(weekday, future_break):
                            continue
                        future_options += 1
                    
                    key = (
                        future_options,               # 1. Bevorzugt Lehrkräfte mit wenig Restoptionen
                        combined_distribution_score,  # 2. Tagesverteilung + Stockwerk-Präferenz
                        ratio + same_day_penalty,     # 3. Soll-Ist-Verhältnis + Strafe
                        assigned,                     # 4. Absolute Anzahl
                        random_bias[t.id],            # 5. Zufalls-Bias
                    )
                    
                    if best_key is None or key < best_key:
                        best_key = key
                        best_t = t
                
                if best_t is not None:
                    if best_t.preferred_floor_id and best_t.preferred_floor_id != floor_id:
                        pref_name = best_t.preferred_floor.name if best_t.preferred_floor else f"ID {best_t.preferred_floor_id}"
                        print(f"[INFO] {best_t.abbreviation} bevorzugt {pref_name}, wird aber mangels Alternativen für Stockwerk {floor_id} berücksichtigt.")
                    return best_t

        return None

    def pick_teacher_fallback_1(d: date, break_index: int, floor_id: int) -> Optional[Teacher]:
        """FALLBACK 1: Nutzt alle verfügbaren Lehrkräfte, respektiert aber Soll-Ziele"""
        weekday = d.weekday()
        
        already = set(
            t_id for (t_id,) in db.query(Assignment.teacher_id)
            .join(DutySlot, DutySlot.id == Assignment.duty_slot_id)
            .filter(DutySlot.date == d, DutySlot.break_index == break_index)
            .all()
        )
        
        candidates = []
        for t, target in eligible:
            if t.id in already:
                continue
            assigned = existing_counts.get(t.id, 0)
            if assigned >= target:
                continue
            if not t.is_available_on_weekday(weekday):
                continue
            if not t.is_available_for_supervision(weekday, break_index):
                continue
            
            existing_breaks = [
                break_idx for (break_idx,) in 
                db.query(DutySlot.break_index)
                .join(Assignment, Assignment.duty_slot_id == DutySlot.id)
                .filter(Assignment.teacher_id == t.id, DutySlot.date == d)
                .all()
            ]
            
            duties_today = len(existing_breaks)

            # Behalte noch Tageslimit bei
            if duties_today >= 2:
                continue
            
            # Behalte noch aufeinanderfolgende Pausen-Regel bei
            has_consecutive = any(abs(eb - break_index) == 1 for eb in existing_breaks)
            if has_consecutive:
                continue
            
            candidates.append((t, duties_today))
        
        def pick_best(entries):
            if not entries:
                return None
            zero = [item for item in entries if item[1] == 0]
            pool = zero if zero else entries
            chosen_entry = min(pool, key=lambda item: (existing_counts.get(item[0].id, 0), item[0].id))
            return chosen_entry[0]

        preferred_candidates = [(t, duty_count) for t, duty_count in candidates if t.preferred_floor_id == floor_id]
        neutral_candidates = [(t, duty_count) for t, duty_count in candidates if t.preferred_floor_id is None]
        conflicting_candidates = [(t, duty_count) for t, duty_count in candidates if t.preferred_floor_id not in (None, floor_id)]

        chosen = pick_best(preferred_candidates)
        if chosen:
            return chosen

        chosen = pick_best(neutral_candidates)
        if chosen:
            return chosen

        chosen = pick_best(conflicting_candidates)
        if chosen:
            pref_name = chosen.preferred_floor.name if chosen.preferred_floor else f"ID {chosen.preferred_floor_id}"
            print(f"[WARN] Fallback 1 überschreibt Präferenz von {chosen.abbreviation} ({pref_name}) für Stockwerk {floor_id}.")
            return chosen
        return None

    def pick_teacher_fallback_2(d: date, break_index: int, floor_id: int) -> Optional[Teacher]:
        """FALLBACK 2: Erlaubt mehr als 2 Aufsichten/Tag, respektiert aber Soll-Ziele"""
        weekday = d.weekday()
        
        already = set(
            t_id for (t_id,) in db.query(Assignment.teacher_id)
            .join(DutySlot, DutySlot.id == Assignment.duty_slot_id)
            .filter(DutySlot.date == d, DutySlot.break_index == break_index)
            .all()
        )
        
        candidates = []
        for t, target in eligible:
            if t.id in already:
                continue
            assigned = existing_counts.get(t.id, 0)
            if assigned >= target:
                continue
            if not t.is_available_on_weekday(weekday):
                continue
            if not t.is_available_for_supervision(weekday, break_index):
                continue
            
            # Ignoriere Tageslimit! Aber behalte aufeinanderfolgende Pausen-Regel
            existing_breaks = [
                break_idx for (break_idx,) in 
                db.query(DutySlot.break_index)
                .join(Assignment, Assignment.duty_slot_id == DutySlot.id)
                .filter(Assignment.teacher_id == t.id, DutySlot.date == d)
                .all()
            ]
            
            has_consecutive = any(abs(eb - break_index) == 1 for eb in existing_breaks)
            if has_consecutive:
                continue
            
            candidates.append((t, len(existing_breaks)))
        
        def pick_best(entries):
            if not entries:
                return None
            zero = [item for item in entries if item[1] == 0]
            pool = zero if zero else entries
            chosen_entry = min(pool, key=lambda item: (existing_counts.get(item[0].id, 0), item[0].id))
            return chosen_entry[0]

        preferred_candidates = [(t, duty_count) for t, duty_count in candidates if t.preferred_floor_id == floor_id]
        neutral_candidates = [(t, duty_count) for t, duty_count in candidates if t.preferred_floor_id is None]
        conflicting_candidates = [(t, duty_count) for t, duty_count in candidates if t.preferred_floor_id not in (None, floor_id)]

        chosen = pick_best(preferred_candidates)
        if chosen:
            return chosen

        chosen = pick_best(neutral_candidates)
        if chosen:
            return chosen

        chosen = pick_best(conflicting_candidates)
        if chosen:
            pref_name = chosen.preferred_floor.name if chosen.preferred_floor else f"ID {chosen.preferred_floor_id}"
            print(f"[WARN] Fallback 2 überschreibt Präferenz von {chosen.abbreviation} ({pref_name}) für Stockwerk {floor_id}.")
            return chosen
        return None

    def pick_teacher_fallback_3(d: date, break_index: int, floor_id: int) -> Optional[Teacher]:
        """FALLBACK 3: NOTFALL - Ignoriere Soll-Ziele, Unterrichtskriterium bleibt verpflichtend"""
        weekday = d.weekday()

        already = set(
            t_id for (t_id,) in db.query(Assignment.teacher_id)
            .join(DutySlot, DutySlot.id == Assignment.duty_slot_id)
            .filter(DutySlot.date == d, DutySlot.break_index == break_index)
            .all()
        )
        
        candidates = []
        for t, target in eligible:
            if t.id in already:
                continue
            if not t.is_available_on_weekday(weekday):
                continue
            if not t.is_available_for_supervision(weekday, break_index):
                continue

            assigned = existing_counts.get(t.id, 0)

            # Ignoriere ALLES außer aufeinanderfolgenden Pausen
            existing_breaks = [
                break_idx for (break_idx,) in
                db.query(DutySlot.break_index)
                .join(Assignment, Assignment.duty_slot_id == DutySlot.id)
                .filter(Assignment.teacher_id == t.id, DutySlot.date == d)
                .all()
            ]

            has_consecutive = any(abs(eb - break_index) == 1 for eb in existing_breaks)
            if has_consecutive:
                continue

            candidates.append((t, len(existing_breaks), assigned, target))

        def pick_best(entries):
            if not entries:
                return None
            zero = [item for item in entries if item[1] == 0]
            pool = zero if zero else entries

            def sort_key(item: Tuple[Teacher, int, int, int]):
                t, duties_today, assigned, target = item
                below_target_flag = 0 if target and assigned < target else 1
                ratio = (assigned / max(target, 1)) if target else assigned
                return (
                    below_target_flag,
                    ratio,
                    duties_today,
                    existing_counts.get(t.id, 0),
                    t.id,
                )

            chosen_entry = min(pool, key=sort_key)
            return chosen_entry[0]

        preferred_candidates = [
            (t, duty_count, assigned, target)
            for t, duty_count, assigned, target in candidates
            if t.preferred_floor_id == floor_id
        ]
        neutral_candidates = [
            (t, duty_count, assigned, target)
            for t, duty_count, assigned, target in candidates
            if t.preferred_floor_id is None
        ]
        conflicting_candidates = [
            (t, duty_count, assigned, target)
            for t, duty_count, assigned, target in candidates
            if t.preferred_floor_id not in (None, floor_id)
        ]

        chosen = pick_best(preferred_candidates)
        if chosen:
            return chosen

        chosen = pick_best(neutral_candidates)
        if chosen:
            return chosen

        chosen = pick_best(conflicting_candidates)
        if chosen:
            pref_name = chosen.preferred_floor.name if chosen.preferred_floor else f"ID {chosen.preferred_floor_id}"
            print(f"[WARN] Fallback 3 überschreibt Präferenz von {chosen.abbreviation} ({pref_name}) für Stockwerk {floor_id}.")
            return chosen
        return None

    floor_required: Dict[int, int] = {}
    for f in db.query(Floor).all():
        floor_required[f.id] = max(1, f.required_per_break or 1)

    # Einfache Sortierung: Chronologisch nach Datum und Pause
    # Alle Slots werden gleichberechtigt behandelt
    sorted_slots = sorted(slots, key=lambda slot: (slot.date, slot.break_index, slot.floor_id))
    
    # MEHRSTUFIGES SCHEDULING: Erst normal, dann mit gelockerten Beschränkungen
    unassigned_slots = []
    
    for slot in sorted_slots:
        current_assigned = (
            db.query(Assignment)
            .filter(Assignment.duty_slot_id == slot.id)
            .count()
        )
        needed = max(0, floor_required.get(slot.floor_id, 1) - current_assigned)
        
        for _ in range(needed):
            t = pick_teacher(slot.date, slot.break_index, slot.floor_id)
            if t is None:
                # Merke unbesetzte Slots für Fallback-Strategien
                unassigned_slots.append(slot)
                break
            db.add(Assignment(duty_slot_id=slot.id, teacher_id=t.id))
            existing_counts[t.id] = existing_counts.get(t.id, 0) + 1
        db.flush()
    
    # FALLBACK-STRATEGIEN für unbesetzte Slots
    if unassigned_slots:
        print(f"[DEBUG] {len(unassigned_slots)} Slots unbesetzt, starte Fallback-Strategien...")
        
        # FALLBACK 1: Verwendet alle verfügbaren Lehrkräfte, Soll-Ziele bleiben Grenze
        for slot in unassigned_slots[:]:  # Kopie der Liste für sicheres Entfernen
            t = pick_teacher_fallback_1(slot.date, slot.break_index, slot.floor_id)
            if t is not None:
                db.add(Assignment(duty_slot_id=slot.id, teacher_id=t.id))
                existing_counts[t.id] = existing_counts.get(t.id, 0) + 1
                unassigned_slots.remove(slot)
                print(f"  [FALLBACK 1] {slot.date} Pause {slot.break_index} Stockwerk {slot.floor_id}: {t.abbreviation}")
        
        db.flush()
        
        # FALLBACK 2: Ignoriere Tageslimits (mehr als 2 Aufsichten/Tag erlaubt)
        if unassigned_slots:
            for slot in unassigned_slots[:]:
                t = pick_teacher_fallback_2(slot.date, slot.break_index, slot.floor_id)
                if t is not None:
                    db.add(Assignment(duty_slot_id=slot.id, teacher_id=t.id))
                    existing_counts[t.id] = existing_counts.get(t.id, 0) + 1
                    unassigned_slots.remove(slot)
                    print(f"  [FALLBACK 2] {slot.date} Pause {slot.break_index} Stockwerk {slot.floor_id}: {t.abbreviation}")
            
            db.flush()
        
        # FALLBACK 3: Ignoriere Unterrichtsstunden (Notfall-Zuweisungen)
        if unassigned_slots:
            for slot in unassigned_slots[:]:
                t = pick_teacher_fallback_3(slot.date, slot.break_index, slot.floor_id)
                if t is not None:
                    db.add(Assignment(duty_slot_id=slot.id, teacher_id=t.id))
                    existing_counts[t.id] = existing_counts.get(t.id, 0) + 1
                    unassigned_slots.remove(slot)
                    print(f"  [FALLBACK 3] {slot.date} Pause {slot.break_index} Stockwerk {slot.floor_id}: {t.abbreviation} (NOTFALL)")
            
            db.flush()

    db.commit()
    
    # Debug: Zeige finale Zuweisungen mit Anwesenheitstag-Info
    print(f"[DEBUG] Scheduling abgeschlossen. Finale Zuweisungen:")
    final_counts = {}
    for t_id, count in existing_counts.items():
        teacher = next((t for t, _ in eligible if t.id == t_id), None)
        if teacher:
            target = teacher_to_target.get(t_id, 0)
            available_days = sum(1 for i in range(5) if teacher.is_available_on_weekday(i))
            attendance_display = teacher.get_actual_attendance_days_display()
            final_counts[teacher.abbreviation] = {
                'count': count,
                'target': target,
                'days': available_days,
                'attendance': attendance_display
            }
    
    # Zeige zuerst Lehrkräfte mit zu wenigen Aufsichten
    underassigned = [(abbrev, data) for abbrev, data in final_counts.items() if data['count'] < data['target']]
    if underassigned:
        print(f"  [UNTERBESETZT] {len(underassigned)} Lehrkräfte unter Soll:")
        for abbrev, data in sorted(underassigned, key=lambda x: x[1]['days'])[:5]:
            print(f"    - {abbrev}: {data['count']}/{data['target']} ({data['days']} Tage, {data['attendance']})")
        if len(underassigned) > 5:
            print(f"    ... und {len(underassigned) - 5} weitere")
    
    # Zeige dann gut verteilte Lehrkräfte
    well_assigned = [(abbrev, data) for abbrev, data in final_counts.items() if data['count'] >= data['target']]
    if well_assigned:
        print(f"  [SOLL ERREICHT] {len(well_assigned)} Lehrkräfte:")
        for abbrev, data in sorted(well_assigned)[:5]:
            print(f"    - {abbrev}: {data['count']}/{data['target']} ({data['attendance']})")
        if len(well_assigned) > 5:
            print(f"    ... und {len(well_assigned) - 5} weitere")
        
    # Zeige unbesetzte Slots
    unassigned_slots = db.query(DutySlot).outerjoin(Assignment).filter(
        Assignment.id.is_(None),
        DutySlot.date >= start_date,
        DutySlot.date <= end_date
    ).count()
    
    if unassigned_slots > 0:
        print(f"[ERROR] {unassigned_slots} Slots konnten NICHT besetzt werden!")
        print("Mögliche Gründe:")
        print("- Zu wenige Lehrkräfte mit Soll-Aufsichten > 0")
        print("- Alle Lehrkräfte haben ungünstige Stundenpläne")
        print("- Zu restriktive Anwesenheitstage-Einstellungen")
    else:
        print(f"[SUCCESS] ALLE Slots erfolgreich besetzt!")
