from __future__ import annotations

from app.services.gpu_import import parse_gpu_line


def test_parse_gpu_line_includes_room_code() -> None:
    parsed = parse_gpu_line('4063;"12ZU4A";"HOO";"ENG";"3035";2;13;;')

    assert parsed == ("HOO", 1, 13, "3035")


def test_parse_gpu_line_allows_empty_room() -> None:
    parsed = parse_gpu_line('4063;"12ZU4A";"HOO";"ENG";"";2;13;;')

    assert parsed == ("HOO", 1, 13, None)
