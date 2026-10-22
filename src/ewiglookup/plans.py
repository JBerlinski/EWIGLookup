"""Plany grup złożone tak jak w gcalsync (gcalsync.config.json: plany, grupy, reguły).

Każdy plan to kilka grup w kolejności priorytetu i reguły wykluczeń. Kolejność kroków jak
w gcalsync (core/pipeline.py): reguły → jeden egzemplarz duplikatu (z ważniejszej grupy) →
odrzucenie zajęć, które nakładają się na zajęcia z ważniejszej grupy (polityka „priority”).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

import requests

from ewiglookup.grid import Lesson

GCALSYNC_CONFIG_URL = (
    "https://raw.githubusercontent.com/JBerlinski/Gcalsync/main/gcalsync.config.json"
)


class PlanConfigError(ValueError):
    pass


def _normalize(value: str) -> str:
    """Jak normalize_text w gcalsync: NFC i zwinięte białe znaki."""
    return " ".join(unicodedata.normalize("NFC", value).split())


@dataclass(frozen=True)
class Rule:
    """Reguła wykluczenia z gcalsync (core/rules.py, ExclusionRule)."""

    field: str  # subject, course, kind, location
    op: str  # contains, equals, regex
    value: str
    case_sensitive: bool = False
    enabled: bool = True
    sources: tuple[str, ...] | None = None  # nazwy grup w planie; None = wszystkie
    date_from: date | None = None
    date_to: date | None = None

    @classmethod
    def from_dict(cls, data: dict) -> Rule:
        rule = cls(
            field=data["field"],
            op=data["op"],
            value=data["value"],
            case_sensitive=bool(data.get("case_sensitive", False)),
            enabled=bool(data.get("enabled", True)),
            sources=tuple(data["sources"]) if data.get("sources") is not None else None,
            date_from=date.fromisoformat(data["date_from"]) if data.get("date_from") else None,
            date_to=date.fromisoformat(data["date_to"]) if data.get("date_to") else None,
        )
        if rule.field not in ("subject", "course", "kind", "location"):
            raise PlanConfigError(f"Nieznane pole reguły: {rule.field!r}")
        if rule.op not in ("contains", "equals", "regex"):
            raise PlanConfigError(f"Nieznany operator reguły: {rule.op!r}")
        return rule

    def _value(self, lesson: Lesson) -> str:
        if self.field == "subject":  # temat z eksportu ewig: „Przedmiot (forma) [numer]”
            return _normalize(f"{lesson.course} ({lesson.kind}) [{lesson.seq}]")
        if self.field == "course":
            return _normalize(lesson.course)
        if self.field == "kind":
            return lesson.kind
        return _normalize(lesson.room)

    def matches(self, lesson: Lesson, source: str) -> bool:
        if not self.enabled or (self.sources is not None and source not in self.sources):
            return False
        if (self.date_from and lesson.day < self.date_from) or (
            self.date_to and lesson.day > self.date_to
        ):
            return False
        text = self._value(lesson)
        if self.op == "regex":
            flags = 0 if self.case_sensitive else re.IGNORECASE
            return re.search(self.value, text, flags) is not None
        needle = _normalize(self.value)
        if not self.case_sensitive:
            text, needle = text.casefold(), needle.casefold()
        return needle in text if self.op == "contains" else needle == text


@dataclass(frozen=True)
class Plan:
    name: str
    groups: tuple[tuple[str, str], ...]  # (kod grupy, nazwa źródła) w kolejności priorytetu
    rules: tuple[Rule, ...]
    keep_all: bool = False  # polityka „keep-all”: kolizje między grupami nie są rozwiązywane


def parse_plans(data: dict) -> list[Plan]:
    """Plany z gcalsync.config.json (wersja 2)."""
    try:
        keep_all = data.get("policy", "priority") == "keep-all"
        return [
            Plan(
                name=p["name"],
                groups=tuple((g["code"], g.get("name", g["code"])) for g in p["groups"]),
                rules=tuple(Rule.from_dict(r) for r in p.get("rules", [])),
                keep_all=keep_all,
            )
            for p in data["plans"]
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise PlanConfigError(f"Niepoprawny gcalsync.config.json: {exc!r}") from exc


def fetch_plans(url: str = GCALSYNC_CONFIG_URL) -> list[Plan]:
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return parse_plans(response.json())
    except (requests.RequestException, ValueError) as exc:
        raise PlanConfigError(f"Nie udało się pobrać konfiguracji gcalsync ({url}): {exc}") from exc


def load_plans(source: str) -> list[Plan]:
    """Plany z gcalsync.config.json — z adresu (http…) albo z pliku."""
    if source.startswith(("http://", "https://")):
        return fetch_plans(source)
    try:
        with open(source, encoding="utf-8") as f:
            return parse_plans(json.load(f))
    except (OSError, ValueError) as exc:
        raise PlanConfigError(f"Nie udało się odczytać {source}: {exc}") from exc


def _minutes(hm: str) -> int:
    hours, minutes = hm.split(":")
    return int(hours) * 60 + int(minutes)


def _overlap(a: Lesson, b: Lesson) -> bool:
    """Nakładanie się w czasie (samo stykanie się nie jest nakładaniem) — jak w gcalsync."""
    return (
        a.day == b.day
        and _minutes(a.start) < _minutes(b.end)
        and _minutes(b.start) < _minutes(a.end)
    )


def plan_lessons(plan: Plan, groups: dict[str, list[Lesson]]) -> list[Lesson]:
    """Zajęcia planu: reguły → deduplikacja → kolizje według priorytetu grup."""
    kept: list[tuple[int, Lesson]] = []
    for rank, (code, source) in enumerate(plan.groups):
        for lesson in groups.get(code, []):
            if not any(rule.matches(lesson, source) for rule in plan.rules):
                kept.append((rank, lesson))
    # jeden egzemplarz tych samych zajęć — z najważniejszej grupy; klucz jak event_key
    # w gcalsync: przedmiot, forma i czas (bez numeru zajęć i sali)
    unique: dict[tuple, tuple[int, Lesson]] = {}
    for rank, lesson in kept:
        key = (lesson.day, lesson.start, lesson.end, _normalize(lesson.course).casefold(),
               _normalize(lesson.kind).casefold())  # fmt: skip
        if key not in unique or rank < unique[key][0]:
            unique[key] = (rank, lesson)
    ordered = sorted(unique.values(), key=lambda item: (item[0], item[1].day, item[1].start))
    accepted: list[tuple[int, Lesson]] = []
    for rank, lesson in ordered:
        if not plan.keep_all and any(r < rank and _overlap(a, lesson) for r, a in accepted):
            continue
        accepted.append((rank, lesson))
    return sorted((lesson for _, lesson in accepted), key=lambda e: (e.day, e.start, e.course))
