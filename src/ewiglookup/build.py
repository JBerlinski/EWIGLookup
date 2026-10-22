"""Pobranie planów wszystkich grup i złożenie z nich planów prowadzących i sal (data.json)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ewiglookup.ewig import (
    EwigClient,
    EwigConnectionError,
    EwigError,
    EwigLoginError,
    EwigSessionError,
    page_hint,
    parse_groups,
)
from ewiglookup.grid import Lesson, count_cells, diagnose, parse_plan_grid
from ewiglookup.plans import GCALSYNC_CONFIG_URL, Plan, plan_lessons

PAUSE_BETWEEN_GROUPS = 2  # s — nie obciążamy ewig
GROUP_RETRY_DELAY = 60  # s — przerwa przed ponowieniem grupy w nowej sesji
DIAGNOSED_GROUPS = 3  # dla ilu grup z niekompletną siatką opisać układ strony w logu
DATA_VERSION = 1


@dataclass(frozen=True)
class Config:
    semester_iid: int
    group_pattern: str = r"[A-Z]{2,4}\d{2}[A-Z0-9]+"
    include: tuple[str, ...] = ()  # grupy dopisane ręcznie (gdy nie ma ich na liście)
    exclude: tuple[str, ...] = ()
    max_failed_share: float = 0.2  # więcej nieudanych grup = błąd, strona zostaje bez zmian
    # gcalsync.config.json (adres albo ścieżka) — plany grup do wyszukiwarki terminów; "" = bez
    gcalsync_config: str = GCALSYNC_CONFIG_URL

    @classmethod
    def load(cls, path: Path) -> Config:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            semester_iid=int(raw["semester_iid"]),
            group_pattern=raw.get("group_pattern", cls.group_pattern),
            include=tuple(raw.get("include", ())),
            exclude=tuple(raw.get("exclude", ())),
            max_failed_share=float(raw.get("max_failed_share", cls.max_failed_share)),
            gcalsync_config=raw.get("gcalsync_config", cls.gcalsync_config) or "",
        )


@dataclass
class Collected:
    groups: dict[str, list[Lesson]] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)  # grupa -> powód
    warnings: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)  # tylko do logu uruchomienia


def _start_session(client: EwigClient, semester_iid: int) -> str:
    client.login()
    return client.menu_page(semester_iid)


def collect(client: EwigClient, config: Config, save_dir: Path | None = None) -> Collected:
    """Plany wszystkich grup z listy w ewig — w jednej sesji, z ponowieniem w nowej sesji."""
    out = Collected()
    iid = config.semester_iid
    try:
        menu = _start_session(client, iid)
        if save_dir:
            save_dir.mkdir(parents=True, exist_ok=True)
            (save_dir / "menu.html").write_text(menu, encoding="utf-8")
        listed = parse_groups(menu, config.group_pattern)
        groups = [g for g in dict.fromkeys([*listed, *config.include]) if g not in config.exclude]
        if not groups:
            raise EwigError(
                f"Na stronie „Rozkład zajęć grupy” nie znaleziono żadnej grupy (wzorzec "
                f"{config.group_pattern!r}) — treść strony: „{page_hint(menu)}”."
            )
        for index, group in enumerate(groups):
            if index:
                client.pause(PAUSE_BETWEEN_GROUPS)
            # Brak połączenia albo odrzucona sesja: jedna próba w nowej sesji; gdy i ona zawiedzie,
            # ewig nie działa — przerywamy całość (strona zostaje bez zmian).
            # Inny błąd (np. pusta strona grupy) pomija tylko tę grupę.
            try:
                try:
                    page = client.group_page(iid, group)
                except (EwigConnectionError, EwigSessionError) as exc:
                    client.notes.append(f"{exc} Ponawiam grupę {group} w nowej sesji.")
                    client.logout()
                    client.pause(GROUP_RETRY_DELAY)
                    _start_session(client, iid)
                    page = client.group_page(iid, group)
            except (EwigConnectionError, EwigSessionError, EwigLoginError):
                raise
            except EwigError as exc:
                out.failed[group] = str(exc)
                continue
            if save_dir:
                (save_dir / f"{group}.html").write_text(page, encoding="utf-8")
            lessons = parse_plan_grid(page, iid)
            cells = count_cells(page)
            if cells != len(lessons):
                out.warnings.append(
                    f"Grupa {group}: siatka odczytana niekompletnie ({len(lessons)} z {cells} "
                    "zajęć na stronie)."
                )
                if len(out.warnings) <= DIAGNOSED_GROUPS:
                    out.diagnostics += [f"{group}: {line}" for line in diagnose(page, iid)]
            out.groups[group] = lessons
    finally:
        client.logout()
    return out


def merge(groups: dict[str, list[Lesson]]) -> list[tuple[Lesson, list[str]]]:
    """Te same zajęcia w planach kilku grup (np. wykład) -> jedna pozycja z listą grup."""
    merged: dict[tuple, tuple[Lesson, list[str]]] = {}
    for group, lessons in groups.items():
        for lesson in lessons:
            key = _key(lesson)
            if key not in merged:
                merged[key] = (lesson, [])
            if group not in merged[key][1]:
                merged[key][1].append(group)
    return sorted(merged.values(), key=lambda item: (item[0].day, item[0].start, item[0].course))


def _index(values: Iterable[str]) -> tuple[list[str], dict[str, int]]:
    ordered = sorted(set(values))
    return ordered, {v: i for i, v in enumerate(ordered)}


def usable_plans(plans: Iterable[Plan], collected: Collected) -> list[Plan]:
    """Plany, których wszystkie grupy pobrano (niepełny plan dawałby fałszywie wolne terminy)."""
    usable = []
    for plan in plans:
        missing = [code for code, _ in plan.groups if code not in collected.groups]
        if missing:
            collected.warnings.append(
                f"Plan {plan.name} pominięty — brak planu grup: {', '.join(missing)}."
            )
        else:
            usable.append(plan)
    return usable


def _key(lesson: Lesson) -> tuple:
    return (lesson.day, lesson.start, lesson.end, lesson.course, lesson.kind, lesson.teacher,
            lesson.room)  # fmt: skip


def to_data(
    collected: Collected,
    semester_iid: int,
    plans: Iterable[Plan] = (),
    generated: datetime | None = None,
) -> dict:
    """Zwarty JSON dla strony: słowniki nazw i zajęcia jako krotki indeksów."""
    lessons = merge(collected.groups)
    position = {_key(lesson): i for i, (lesson, _) in enumerate(lessons)}
    teachers, t_idx = _index(lesson.teacher for lesson, _ in lessons)
    rooms, r_idx = _index(lesson.room for lesson, _ in lessons)
    courses, c_idx = _index(lesson.course for lesson, _ in lessons)
    groups, g_idx = _index(collected.groups)
    kinds = {lesson.kind: lesson.kind_name for lesson, _ in lessons if lesson.kind_name}
    return {
        "version": DATA_VERSION,
        "generated": (generated or datetime.now(UTC)).isoformat(timespec="seconds"),
        "semester": semester_iid,
        "groups": groups,
        "failed": sorted(collected.failed),
        "teachers": teachers,
        "rooms": rooms,
        "courses": courses,
        "kinds": dict(sorted(kinds.items())),
        # [data, początek, koniec, przedmiot, forma, numer, prowadzący, sala, [grupy]]
        "lessons": [
            [
                lesson.day.isoformat(),
                lesson.start,
                lesson.end,
                c_idx[lesson.course],
                lesson.kind,
                lesson.seq,
                t_idx[lesson.teacher],
                r_idx[lesson.room],
                sorted(g_idx[g] for g in in_groups),
            ]
            for lesson, in_groups in lessons
        ],
        # plany grup jak w gcalsync: nazwa i numery zajęć z listy "lessons"
        "plans": [
            {
                "name": plan.name,
                "lessons": [position[_key(e)] for e in plan_lessons(plan, collected.groups)],
            }
            for plan in plans
        ],
    }


def check(collected: Collected, config: Config) -> None:
    """Za dużo nieudanych grup albo brak zajęć -> błąd: lepiej zostawić poprzednią wersję."""
    total = len(collected.groups) + len(collected.failed)
    if not any(collected.groups.values()):
        raise EwigError("Nie odczytano żadnych zajęć — strona nie zostanie zaktualizowana.")
    if total and len(collected.failed) / total > config.max_failed_share:
        reasons = "; ".join(f"{g}: {r}" for g, r in list(collected.failed.items())[:3])
        raise EwigError(
            f"Nie pobrano {len(collected.failed)} z {total} grup ({reasons}) — strona nie "
            "zostanie zaktualizowana."
        )


def summary(collected: Collected, data: dict, notes: list[str]) -> str:
    """Podsumowanie uruchomienia (Markdown, do raportu GitHub Actions)."""
    lines = [
        "## Plany prowadzących i sal",
        "",
        f"- grupy: {len(collected.groups)} pobrane, {len(collected.failed)} nieudane",
        f"- zajęcia: {len(data['lessons'])}",
        f"- prowadzący: {sum(1 for t in data['teachers'] if t)}",
        f"- sale: {sum(1 for r in data['rooms'] if r)}",
        "- plany grup (gcalsync): "
        + (", ".join(f"{p['name']} ({len(p['lessons'])} zajęć)" for p in data["plans"]) or "brak"),
    ]
    if collected.failed:
        lines += ["", "### Nieudane grupy", ""]
        lines += [f"- {g}: {reason}" for g, reason in collected.failed.items()]
    if collected.warnings or notes:
        lines += ["", "### Uwagi", ""]
        lines += [f"- {w}" for w in [*collected.warnings, *notes]]
    return "\n".join(lines) + "\n"
