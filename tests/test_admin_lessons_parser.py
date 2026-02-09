from __future__ import annotations

import pytest

from app.routers.admin import parse_hours_input, parse_checkbox_hours


def test_parse_hours_input_accepts_single_values_and_ranges() -> None:
    assert parse_hours_input("1,2, 5;7-9") == [1, 2, 5, 7, 8, 9]


def test_parse_hours_input_deduplicates_and_sorts() -> None:
    assert parse_hours_input("3,2,3,2-4") == [2, 3, 4]


def test_parse_hours_input_accepts_empty_value() -> None:
    assert parse_hours_input("") == []
    assert parse_hours_input("   ") == []


@pytest.mark.parametrize(
    "raw",
    [
        "0",
        "21",
        "a",
        "3-b",
        "7-2",
    ],
)
def test_parse_hours_input_rejects_invalid_tokens(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_hours_input(raw)


def test_parse_checkbox_hours_accepts_values() -> None:
    assert parse_checkbox_hours(["1", "2", "2", "20"]) == [1, 2, 20]


@pytest.mark.parametrize("raw_values", [["0"], ["21"], ["x"]])
def test_parse_checkbox_hours_rejects_invalid_values(raw_values: list[str]) -> None:
    with pytest.raises(ValueError):
        parse_checkbox_hours(raw_values)
