from __future__ import annotations

from dataclasses import dataclass

from app.services.scheduler import _adjust_targets_for_total_need


@dataclass
class _Spec:
    id: int
    nominal_target: int
    availability_days: int
    target: int


@dataclass
class _Slot:
    needs: dict[int, int]


def test_adjust_targets_scales_down_proportionally_on_overcoverage() -> None:
    teacher_specs = [
        _Spec(id=1, nominal_target=3, availability_days=5, target=3),
        _Spec(id=2, nominal_target=3, availability_days=5, target=3),
        _Spec(id=3, nominal_target=3, availability_days=5, target=3),
        _Spec(id=4, nominal_target=2, availability_days=5, target=2),
        _Spec(id=5, nominal_target=1, availability_days=5, target=1),
    ]
    break_slots = [_Slot(needs={1: 8})]

    adjustments = _adjust_targets_for_total_need(teacher_specs, break_slots)

    assert sum(spec.target for spec in teacher_specs) == 8
    assert [spec.target for spec in teacher_specs] == [2, 2, 2, 1, 1]
    assert adjustments == {1: -1, 2: -1, 3: -1, 4: -1}


def test_adjust_targets_scales_up_by_availability_on_undercoverage() -> None:
    teacher_specs = [
        _Spec(id=1, nominal_target=1, availability_days=5, target=1),
        _Spec(id=2, nominal_target=1, availability_days=2, target=1),
        _Spec(id=3, nominal_target=1, availability_days=1, target=1),
    ]
    break_slots = [_Slot(needs={1: 6})]

    adjustments = _adjust_targets_for_total_need(teacher_specs, break_slots)

    assert sum(spec.target for spec in teacher_specs) == 6
    assert [spec.target for spec in teacher_specs] == [3, 2, 1]
    assert adjustments == {1: 2, 2: 1}


def test_adjust_targets_overcoverage_tie_break_is_not_input_order() -> None:
    teacher_specs = [
        _Spec(id=1, nominal_target=3, availability_days=5, target=3),
        _Spec(id=2, nominal_target=3, availability_days=5, target=3),
        _Spec(id=3, nominal_target=3, availability_days=5, target=3),
        _Spec(id=4, nominal_target=3, availability_days=5, target=3),
        _Spec(id=5, nominal_target=3, availability_days=5, target=3),
    ]
    break_slots = [_Slot(needs={1: 13})]

    adjustments = _adjust_targets_for_total_need(teacher_specs, break_slots)

    assert sum(spec.target for spec in teacher_specs) == 13
    # Bei Gleichstand darf die Auswahl nicht über die Eingabereihenfolge laufen.
    assert [spec.target for spec in teacher_specs][:3] != [3, 3, 3]
    assert sorted(spec.target for spec in teacher_specs) == [2, 2, 3, 3, 3]
    assert sum(adjustments.values()) == -2
