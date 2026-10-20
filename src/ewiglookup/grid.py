"""Zajęcia z siatki na stronie planu grupy ewig (parser przeniesiony z gcalsync, ewig_plan.py).

Strona planu grupy zawiera pełną siatkę semestru:

- dla każdego dnia tygodnia blok: wiersz nagłówka z datami („30<br>IX”, „07<br>X”, …),
  potem wiersze bloków zajęć: numer, godziny („08:00<br>09:35”) i po jednej komórce na datę;
- komórka z zajęciami ma zagnieżdżoną tabelę z tytułem „Przedmiot - Prowadzący (Forma)”,
  skrótem formy „(<b>L</b>)”, salą po nim i numerem zajęć „[n]”.

W odróżnieniu od gcalsync zachowujemy prowadzącego i salę — z nich powstają plany
prowadzących i sal.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date

TAG = re.compile(r"<(/?)([a-zA-Z]+)\b([^>]*)>")
CLASS_ATTR = re.compile(r"""\bclass\s*=\s*(["']?)([^"'\s>]+)\1""", re.I)
TITLE_ATTR = re.compile(r"""\btitle\s*=\s*(["'])(.*?)\1""", re.I | re.S)
KIND = re.compile(r"\(\s*<b\b[^>]*>\s*([^<]+?)\s*</b\s*>\s*\)", re.I)
ROOM = re.compile(r"\(\s*<b\b[^>]*>[^<]*</b\s*>\s*\)\s*<br\s*/?>\s*([^<]*?)\s*</nobr", re.I)
SEQ = re.compile(r"<nobr\b[^>]*>\s*\[(\d+)\]\s*</nobr\s*>", re.I)
DATE = re.compile(r"(\d{1,2})\s*<br\s*/?>\s*([IVX]+)", re.I)
TIMES = re.compile(r"(\d{1,2}:\d{2})\s*<br\s*/?>\s*(\d{1,2}:\d{2})", re.I)
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9,
         "X": 10, "XI": 11, "XII": 12}  # fmt: skip


@dataclass(frozen=True)
class Lesson:
    course: str
    kind: str  # skrót formy, np. w, L, ć
    kind_name: str  # pełna nazwa z tytułu, np. Wykład
    seq: int
    teacher: str
    room: str
    day: date
    start: str  # GG:MM
    end: str


@dataclass
class _Cell:
    cls: str
    html: str


def _text(value: str) -> str:
    return " ".join(html.unescape(value).split())


def _grid_table(page: str) -> str | None:
    """Treść tabeli z siatką planu — tej, w której jest komórka dnia tygodnia (klasa *HTM0)."""
    starts: list[int] = []  # początki treści otwartych tabel
    grid_start: int | None = None
    for m in TAG.finditer(page):
        closing, name, attrs = m.group(1) == "/", m.group(2).lower(), m.group(3)
        if name == "table":
            if not closing:
                starts.append(m.end())
            elif starts:
                start = starts.pop()
                if start == grid_start:
                    return page[start : m.start()]
            continue
        if grid_start is None and not closing and name == "td" and starts:
            cls_match = CLASS_ATTR.search(attrs)
            if cls_match and "SheTeaGrpHTM0" in cls_match.group(2):
                grid_start = starts[-1]
    return page[grid_start:] if grid_start is not None else None


def _rows(page: str) -> list[list[_Cell]]:
    """Wiersze tabeli z siatką; komórki z zagnieżdżonymi tabelami (zajęcia) jako surowy HTML."""
    table = _grid_table(page)
    if table is None:
        return []
    depth = 0
    rows: list[list[_Cell]] = []
    row: list[_Cell] | None = None
    cell_start: int | None = None
    cell_cls = ""

    def close_cell(pos: int) -> None:
        nonlocal cell_start
        if row is not None and cell_start is not None:
            row.append(_Cell(cell_cls, table[cell_start:pos]))
        cell_start = None

    for m in TAG.finditer(table):
        closing, name, attrs = m.group(1) == "/", m.group(2).lower(), m.group(3)
        if name == "table":
            depth += -1 if closing else 1
            continue
        if depth or name not in ("tr", "td", "th"):
            continue
        if name == "tr":
            close_cell(m.start())
            if row is not None and (closing or row):
                rows.append(row)
            row = None if closing else []
        elif closing:
            close_cell(m.start())
        else:
            close_cell(m.start())
            if row is None:
                row = []
            cls_match = CLASS_ATTR.search(attrs)
            cell_start, cell_cls = m.end(), cls_match.group(2) if cls_match else ""
    close_cell(len(table))
    if row:
        rows.append(row)
    return rows


def year_for(month: int, semester_iid: int) -> int:
    start_year, semester = divmod(semester_iid, 10)
    if semester == 1:  # zimowy: wrzesień–grudzień w roku startu, styczeń–marzec w następnym
        return start_year if month >= 8 else start_year + 1
    return start_year + 1  # letni: luty–wrzesień roku następnego


def parse_cell(cell_html: str) -> tuple[str, str, str, int, str, str] | None:
    """(przedmiot, skrót formy, nazwa formy, numer, prowadzący, sala) z tabeli zajęć."""
    title = TITLE_ATTR.search(cell_html)
    kind, seq, room = KIND.search(cell_html), SEQ.search(cell_html), ROOM.search(cell_html)
    if not (title and kind and seq):
        return None
    full = _text(title.group(2))
    head, sep, kind_name = full.rpartition(" (")
    course, dash, teacher = head.rpartition(" - ")
    if not dash:
        course, teacher = head, ""
    if not sep or not course:
        return None
    return (
        course.strip(),
        kind.group(1).strip(),
        kind_name.rstrip(")").strip(),
        int(seq.group(1)),
        teacher.strip(),
        _text(room.group(1)) if room else "",
    )


def parse_plan_grid(page: str, semester_iid: int) -> list[Lesson]:
    """Zajęcia z siatki planu grupy, posortowane chronologicznie."""
    lessons: list[Lesson] = []
    dates: list[date | None] = []
    for row in _rows(page):
        header = [c for c in row if "DDSheTeaGrpHTM3" in c.cls]
        if header:
            dates = []
            for c in header:
                d = DATE.search(c.html)
                month = ROMAN.get(d.group(2).upper()) if d else None
                if d and month:
                    dates.append(date(year_for(month, semester_iid), month, int(d.group(1))))
                else:
                    dates.append(None)
            continue
        times = next((TIMES.search(c.html) for c in row if TIMES.search(c.html)), None)
        if times is None:
            continue
        slots = [c for c in row if c.cls.endswith("SheTeaGrpHTM3") and "DD" not in c.cls]
        for day, cell in zip(dates, slots, strict=False):
            if day is None or "<table" not in cell.html.lower():
                continue
            for inner in re.findall(r"<table\b.*?</table\s*>", cell.html, re.S | re.I):
                parsed = parse_cell(inner)
                if parsed:
                    course, kind, kind_name, seq, teacher, room = parsed
                    start, end = (t.zfill(5) for t in times.groups())
                    lessons.append(
                        Lesson(course, kind, kind_name, seq, teacher, room, day, start, end)
                    )
    lessons.sort(key=lambda e: (e.day, e.start, e.course))
    return lessons


def count_cells(page: str) -> int:
    """Liczba tabel zajęć na stronie — do kontroli, czy siatka odczytała wszystkie."""
    tables = re.findall(r"<table\b[^>]*\btitle\s*=.*?</table\s*>", page, re.S | re.I)
    return sum(1 for t in tables if parse_cell(t))
