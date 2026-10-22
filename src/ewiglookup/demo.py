"""Zmyślone dane w formacie data.json — do podglądu strony bez dostępu do ewig."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import date, timedelta

from ewiglookup.build import Collected, ewig_plan
from ewiglookup.gcal import PlanLesson
from ewiglookup.grid import Lesson
from ewiglookup.plans import Plan, Rule

# plany grup jak w gcalsync.config.json (grupa kierunkowa ważniejsza od domyślnej)
DEMO_PLANS = [
    Plan(
        "WIG23IX2S1",
        (("WIG23IX2S1", "Grupa kierunkowa"), ("WIG23IX1S1", "Grupa domyślna")),
        (Rule("course", "equals", "Statystyka"),),
    ),
    Plan("WIG23IX1S1", (("WIG23IX1S1", "Grupa WIG23IX1S1"),), ()),
]

SLOTS = [("08:00", "09:35"), ("09:50", "11:25"), ("11:40", "13:15"), ("13:30", "15:05"),
         ("15:45", "17:20"), ("17:35", "19:10")]  # fmt: skip
KINDS = [("w", "Wykład"), ("ć", "Ćwiczenia"), ("L", "Laboratorium"), ("P", "Projekt")]
COURSES = ["Geodezja wyższa", "Fotogrametria", "Systemy informacji przestrzennej",
           "Teledetekcja", "Kartografia cyfrowa", "Geowizualizacja", "Bazy danych",
           "Analiza przestrzenna", "Programowanie w Pythonie", "Statystyka",
           "Fizyka", "Matematyka", "Geomatyka", "Seminarium dyplomowe"]  # fmt: skip
FIRST = ["Anna", "Jan", "Piotr", "Maria", "Tomasz", "Ewa", "Krzysztof", "Agnieszka",
         "Paweł", "Katarzyna", "Michał", "Barbara"]  # fmt: skip
LAST = ["Testowy", "Przykładowy", "Zmyślony", "Demo", "Fikcyjny", "Wzorcowy", "Próbny",
        "Makieta", "Atrapa", "Szablonowy"]  # fmt: skip
TITLES = ["dr inż.", "mgr inż.", "dr hab. inż.", "prof. dr hab.", "dr"]


def demo_collected(seed: int = 7, start: date = date(2026, 10, 1), weeks: int = 16) -> Collected:
    rng = random.Random(seed)
    teachers = sorted(
        {f"{rng.choice(TITLES)} {rng.choice(FIRST)} {rng.choice(LAST)}" for _ in range(30)}
    )
    rooms = sorted({f"{rng.choice([14, 18, 57, 100])} {rng.randint(1, 160)}" for _ in range(25)})
    groups = [f"WIG{y}{k}X{n}S1" for y in (23, 24, 25) for k in ("I", "G") for n in (1, 2)]
    out = Collected()
    monday = start - timedelta(days=start.weekday())
    for group in groups:
        lessons = []
        # stały tygodniowy rozkład grupy, część zajęć co drugi tydzień
        pattern = [
            (rng.randrange(5), rng.randrange(len(SLOTS)), rng.choice(COURSES), rng.choice(KINDS),
             rng.choice(teachers), rng.choice(rooms), rng.choice([1, 1, 2]))
            for _ in range(rng.randint(8, 13))
        ]  # fmt: skip
        for week in range(weeks):
            for weekday, slot, course, (kind, kind_name), teacher, room, every in pattern:
                if week % every:
                    continue
                day = monday + timedelta(days=7 * week + weekday)
                if day < start:
                    continue
                s, e = SLOTS[slot]
                lessons.append(Lesson(course, kind, kind_name, week + 1, teacher, room, day, s, e))
        out.groups[group] = lessons
    # wspólny wykład kilku grup — na stronie jedna pozycja z listą grup
    shared = Lesson("Geodezja wyższa", "w", "Wykład", 1, teachers[0], rooms[0], start, *SLOTS[0])
    for group in groups[:3]:
        out.groups[group].append(shared)
    return out


def demo_calendars(collected: Collected) -> dict[str, list[PlanLesson]]:
    """Plan WIG23IX2S1 „z Kalendarza Google”: jedne zajęcia przeniesione ręcznie, jedne dodane."""
    plan = ewig_plan(DEMO_PLANS[0], collected.groups)
    moved = plan[5]
    plan[5] = PlanLesson(
        replace(moved.lesson, start="17:35", end="19:10", room="18 58"),
        moved.groups,
        "zmieniono ręcznie: czas, sala",
    )
    first = plan[0].lesson
    extra = Lesson("Konsultacje", "", "", 0, "", "", first.day, "19:25", "21:00")
    plan.append(PlanLesson(extra, (), "dodane ręcznie"))
    return {DEMO_PLANS[0].name: plan}
