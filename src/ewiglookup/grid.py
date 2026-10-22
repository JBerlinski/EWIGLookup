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
    start: int  # położenie treści komórki na stronie
    end: int
    html: str


def _text(value: str) -> str:
    return " ".join(html.unescape(value).split())


def _grid_tables(page: str) -> list[tuple[int, int]]:
    """Położenie treści tabel z siatką planu — tych, w których są komórki dni tygodnia
    (klasa *HTM0). Bywa ich kilka na stronie (wtedy każda ma własne bloki dni i daty)."""
    starts: list[int] = []  # początki treści otwartych tabel
    grids: set[int] = set()
    found: list[tuple[int, int]] = []
    for m in TAG.finditer(page):
        closing, name, attrs = m.group(1) == "/", m.group(2).lower(), m.group(3)
        if name == "table":
            if not closing:
                starts.append(m.end())
            elif starts:
                start = starts.pop()
                if start in grids:
                    found.append((start, m.start()))
            continue
        if not closing and name == "td" and starts and starts[-1] not in grids:
            cls_match = CLASS_ATTR.search(attrs)
            if cls_match and "SheTeaGrpHTM0" in cls_match.group(2):
                grids.add(starts[-1])
    # niezamknięta tabela siatki (urwana strona) — do końca strony
    found += [(start, len(page)) for start in starts if start in grids]
    return sorted(found)


def _rows(page: str, start: int, end: int) -> list[list[_Cell]]:
    """Wiersze tabeli z siatką; komórki z zagnieżdżonymi tabelami (zajęcia) jako surowy HTML."""
    depth = 0
    rows: list[list[_Cell]] = []
    row: list[_Cell] | None = None
    cell_start: int | None = None
    cell_cls = ""

    def close_cell(pos: int) -> None:
        nonlocal cell_start
        if row is not None and cell_start is not None:
            row.append(_Cell(cell_cls, cell_start, pos, page[cell_start:pos]))
        cell_start = None

    for m in TAG.finditer(page, start, end):
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
    close_cell(end)
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


INNER_TABLE = re.compile(r"<table\b.*?</table\s*>", re.S | re.I)
LESSON_TABLE = re.compile(r"<table\b[^>]*\btitle\s*=.*?</table\s*>", re.S | re.I)


@dataclass
class _Parsed:
    lessons: list[Lesson]
    used: set[int]  # położenia tabel zajęć przypisanych do daty i godziny
    notes: list[str]  # niezgodności układu siatki (do diagnostyki)


def _parse(page: str, semester_iid: int) -> _Parsed:
    out = _Parsed([], set(), [])
    for number, (start, end) in enumerate(_grid_tables(page), 1):
        dates: list[date | None] = []
        for index, row in enumerate(_rows(page, start, end)):
            times = next((TIMES.search(c.html) for c in row if TIMES.search(c.html)), None)
            # Wiersz dat: komórki *HTM3 z datami, bez godzin. Daty poniedziałków są w wierszu
            # nagłówka tabeli (<th class="thFormList1HSheTeaGrpHTM3">), pozostałych dni
            # w komórkach tdFormList1DDSheTeaGrpHTM3.
            header = [c for c in row if c.cls.endswith("SheTeaGrpHTM3")]
            if times is None and any(DATE.search(c.html) for c in header):
                dates = []
                for c in header:
                    d = DATE.search(c.html)
                    month = ROMAN.get(d.group(2).upper()) if d else None
                    if d and month:
                        dates.append(date(year_for(month, semester_iid), month, int(d.group(1))))
                    else:
                        dates.append(None)
                continue
            if times is None:
                continue
            slots = [c for c in header if "DD" not in c.cls and not c.cls.startswith("th")]
            if len(slots) != len(dates):
                classes = ", ".join(sorted({c.cls for c in row}))
                out.notes.append(
                    f"tabela {number}, wiersz {index} ({times.group(1)}): {len(slots)} komórek "
                    f"na {len(dates)} dat, {len(row)} komórek w wierszu; klasy: {classes}"
                )
            for day, cell in zip(dates, slots, strict=False):
                if day is None or "<table" not in cell.html.lower():
                    continue
                for inner in INNER_TABLE.finditer(cell.html):
                    parsed = parse_cell(inner.group(0))
                    if parsed:
                        course, kind, kind_name, seq, teacher, room = parsed
                        begin, finish = (t.zfill(5) for t in times.groups())
                        out.lessons.append(
                            Lesson(course, kind, kind_name, seq, teacher, room, day, begin, finish)
                        )
                        own = LESSON_TABLE.search(inner.group(0))  # bez tabel-opakowań
                        out.used.add(cell.start + inner.start() + (own.start() if own else 0))
    out.lessons.sort(key=lambda e: (e.day, e.start, e.course))
    return out


def parse_plan_grid(page: str, semester_iid: int) -> list[Lesson]:
    """Zajęcia z siatki planu grupy, posortowane chronologicznie."""
    return _parse(page, semester_iid).lessons


def count_cells(page: str) -> int:
    """Liczba tabel zajęć na stronie — do kontroli, czy siatka odczytała wszystkie."""
    return sum(1 for t in LESSON_TABLE.findall(page) if parse_cell(t))


def _where(page: str, pos: int, grids: list[tuple[int, int]]) -> str:
    """Opis miejsca tabeli zajęć: w której tabeli siatki, w jakiej komórce i wierszu."""
    for number, (start, end) in enumerate(grids, 1):
        if not start <= pos < end:
            continue
        for index, row in enumerate(_rows(page, start, end)):
            for col, cell in enumerate(row):
                if cell.start <= pos < cell.end:
                    times = next((TIMES.search(c.html) for c in row if TIMES.search(c.html)), None)
                    when = times.group(1) if times else "bez godzin"
                    return (
                        f"tabela {number}, wiersz {index} ({when}), komórka {col}/{len(row)} "
                        f"klasy {cell.cls or '-'}"
                    )
        return f"tabela {number}, poza komórkami wierszy"
    before = re.sub(r"\s+", " ", page[max(0, pos - 160) : pos])
    return f"poza siatką; wcześniej: …{before}"


def diagnose(page: str, semester_iid: int, limit: int = 8) -> list[str]:
    """Dlaczego część zajęć ze strony nie trafiła do siatki — opis układu (bez danych sesji)."""
    grids = _grid_tables(page)
    parsed = _parse(page, semester_iid)
    classes: dict[str, int] = {}
    for start, end in grids:
        for row in _rows(page, start, end):
            for cell in row:
                classes[cell.cls] = classes.get(cell.cls, 0) + 1
    lines = [
        f"tabel siatki: {len(grids)}; klasy komórek: "
        + ", ".join(f"{k or '-'}×{n}" for k, n in sorted(classes.items()))
    ]
    lines += parsed.notes[:limit]
    missing = [m for m in LESSON_TABLE.finditer(page) if m.start() not in parsed.used]
    missing = [m for m in missing if parse_cell(m.group(0))]
    for m in missing[:limit]:
        course, kind, _name, seq, _teacher, _room = parse_cell(m.group(0))
        lines.append(f"brak: {course} ({kind}) [{seq}] — {_where(page, m.start(), grids)}")
    if len(missing) > limit:
        lines.append(f"… i {len(missing) - limit} więcej")
    return lines
