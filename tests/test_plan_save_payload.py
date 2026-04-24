from __future__ import annotations

from app.routers.plan import TeacherAssignmentData


def test_save_payload_accepts_stable_ids_without_legacy_names() -> None:
    payload = TeacherAssignmentData(
        day=0,
        break_index=2,
        floor="EG",
        floor_id=3,
        teacher_ids=[10, 11],
    )

    assert payload.floor_id == 3
    assert payload.teacher_ids == [10, 11]
    assert payload.teachers == []


def test_save_payload_keeps_legacy_names_for_old_clients() -> None:
    payload = TeacherAssignmentData(
        day=0,
        break_index=2,
        floor="EG",
        teachers=["ABC"],
    )

    assert payload.floor_id is None
    assert payload.teacher_ids == []
    assert payload.teachers == ["ABC"]
