from __future__ import annotations

from app.models import Teacher, TeacherLesson, normalize_room_key
from app.routers.plan import _room_floor_hint_for_teacher


def test_room_floor_hint_matches_adjacent_lesson_room() -> None:
    teacher = Teacher(id=1, first_name="Ada", last_name="Lovelace", abbreviation="LOV")
    teacher.lessons = [
        TeacherLesson(teacher_id=1, weekday=0, hour=2, room="R101"),
        TeacherLesson(teacher_id=1, weekday=0, hour=5, room="R202"),
    ]
    room_floor_by_key = {normalize_room_key("r101"): 4}

    assert _room_floor_hint_for_teacher(teacher, 0, 2, 4, room_floor_by_key) == "R101"


def test_room_floor_hint_ignores_non_matching_floor() -> None:
    teacher = Teacher(id=1, first_name="Ada", last_name="Lovelace", abbreviation="LOV")
    teacher.lessons = [TeacherLesson(teacher_id=1, weekday=0, hour=2, room="R101")]
    room_floor_by_key = {normalize_room_key("R101"): 4}

    assert _room_floor_hint_for_teacher(teacher, 0, 2, 2, room_floor_by_key) is None
