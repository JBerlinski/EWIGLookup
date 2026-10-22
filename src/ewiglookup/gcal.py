"""Plany naszych grup prosto z Kalendarza Google (kalendarze gcalsync).

Kalendarz Google jest jedynym miejscem edycji planów naszych grup: ręczne przeniesienia,
zmiany sal, usunięte i dodane zajęcia (także przez osoby, którym udostępniono kalendarz).
Dlatego plan grupy na stronie to zawartość jej kalendarza, a nie plan z ewig.

Dostęp: token OAuth gcalsync (GOOGLE_TOKEN_JSON — zawartość token.json, zakres
calendar.app.created obejmuje kalendarze utworzone przez gcalsync). Tylko odczyt.

Ręczne zmiany rozpoznajemy jak gcalsync (gcal/mapping.py): zdarzenie gcalsync ma
w extendedProperties.private skróty grup pól (gcalsync_h) z chwili zapisu; inna wartość
skrótu = pole zmienione ręcznie. Zdarzenie bez znacznika gcalsync dodał człowiek.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import requests

from ewiglookup.grid import Lesson

WARSAW = ZoneInfo("Europe/Warsaw")
TOKEN_URI = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{}/events"
TIMEOUT = (15, 60)

# jak w gcalsync: grupy pól, które można zmienić ręcznie (gcal/mapping.py, MANUAL_GROUPS)
MANUAL_GROUPS = {
    "czas": ("start", "end"),
    "sala": ("location",),
    "tytuł": ("summary",),
    "opis": ("description",),
}
TITLE = re.compile(r"^(.*\S)\s*\(([^()]+)\)\s*$")  # „Przedmiot (forma)”
DESCRIPTION_FIELDS = {
    "Przedmiot": "course",
    "Typ zajęć": "kind",
    "Prowadzący": "teacher",
    "Zajęcia nr": "seq",
    "Źródło": "source",
}


class CalendarError(Exception):
    pass


@dataclass(frozen=True)
class PlanLesson:
    """Zajęcia w planie grupy: z kalendarza albo (bez dostępu do niego) z ewig."""

    lesson: Lesson
    groups: tuple[str, ...]  # kody grup, z których pochodzą zajęcia ("" = nieznane)
    note: str = ""  # np. „zmieniono ręcznie: czas, sala”, „dodane ręcznie”


# --- dostęp do Google ----------------------------------------------------------------------


def access_token(token_json: str, session: requests.Session) -> str:
    """Krótki token dostępu z refresh tokenu z token.json gcalsync."""
    try:
        info = json.loads(token_json)
        data = {
            "client_id": info["client_id"],
            "client_secret": info["client_secret"],
            "refresh_token": info["refresh_token"],
            "grant_type": "refresh_token",
        }
    except (ValueError, KeyError, TypeError) as exc:
        raise CalendarError(f"Niepoprawny GOOGLE_TOKEN_JSON ({type(exc).__name__}).") from exc
    try:
        response = session.post(info.get("token_uri") or TOKEN_URI, data=data, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise CalendarError(f"Brak połączenia z Google: {exc}") from exc
    if response.status_code != 200:
        raise CalendarError(
            f"Google odrzucił token (HTTP {response.status_code}) — sprawdź GOOGLE_TOKEN_JSON."
        )
    return response.json()["access_token"]


def list_events(
    session: requests.Session, token: str, calendar_id: str, start: date, end: date
) -> list[dict[str, Any]]:
    """Zdarzenia kalendarza od początku dnia `start` do końca dnia `end` (bez usuniętych)."""
    url = EVENTS_URL.format(requests.utils.quote(calendar_id, safe=""))
    params: dict[str, Any] = {
        "singleEvents": "true",
        "showDeleted": "false",
        "maxResults": 2500,
        "timeMin": datetime.combine(start, time(), WARSAW).isoformat(),
        "timeMax": datetime.combine(end, time(23, 59, 59), WARSAW).isoformat(),
    }
    events: list[dict[str, Any]] = []
    while True:
        try:
            response = session.get(
                url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT
            )
        except requests.RequestException as exc:
            raise CalendarError(f"Brak połączenia z Google Calendar: {exc}") from exc
        if response.status_code != 200:
            raise CalendarError(
                f"Google Calendar zwrócił HTTP {response.status_code} dla kalendarza planu."
            )
        page = response.json()
        events += page.get("items", [])
        if not page.get("nextPageToken"):
            return events
        params["pageToken"] = page["nextPageToken"]


# --- zdarzenie -> zajęcia ---------------------------------------------------------------------


def _parse_time(value: dict[str, Any]) -> datetime | None:
    """Jak parse_time w gcalsync: czas ze strefą (bez offsetu — strefa z pola timeZone)."""
    if "dateTime" in value:
        moment = datetime.fromisoformat(value["dateTime"])
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ZoneInfo(value.get("timeZone") or "Europe/Warsaw"))
        return moment
    if "date" in value:
        return datetime.combine(date.fromisoformat(value["date"]), time(), tzinfo=WARSAW)
    return None


def _time_value(value: dict[str, Any]) -> str:
    if "date" in value and "dateTime" not in value:
        return f"cały dzień {value['date']}"
    moment = _parse_time(value)
    return moment.astimezone(UTC).isoformat() if moment else ""


def group_hashes(event: dict[str, Any]) -> dict[str, str]:
    """Skróty grup pól — ten sam algorytm co gcalsync (comparable + group_hashes)."""
    values = {
        "summary": event.get("summary", ""),
        "location": event.get("location", ""),
        "description": event.get("description", ""),
        "start": _time_value(event.get("start", {})),
        "end": _time_value(event.get("end", {})),
    }
    return {
        group: hashlib.sha256("\x1f".join(values[f] for f in fields).encode("utf-8")).hexdigest()[
            :12
        ]
        for group, fields in MANUAL_GROUPS.items()
    }


def _private(event: dict[str, Any]) -> dict[str, str]:
    return event.get("extendedProperties", {}).get("private", {}) or {}


def manual_changes(event: dict[str, Any]) -> str:
    """Opis ręcznych zmian zdarzenia: "" (bez zmian), „dodane ręcznie”, „zmieniono ręcznie: …”."""
    props = _private(event)
    if props.get("gcalsync_managed") != "1":
        return "dodane ręcznie"
    stored = dict(
        part.split("=", 1) for part in (props.get("gcalsync_h") or "").split(";") if "=" in part
    )
    if not stored:  # zdarzenie sprzed śledzenia zmian — nie da się stwierdzić
        return ""
    current = group_hashes(event)
    changed = [
        group for group in MANUAL_GROUPS if group in stored and stored[group] != current[group]
    ]
    return f"zmieniono ręcznie: {', '.join(changed)}" if changed else ""


def _description(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        label, sep, value = line.partition(":")
        if sep and label.strip() in DESCRIPTION_FIELDS:
            found.setdefault(DESCRIPTION_FIELDS[label.strip()], value.strip())
    return found


def event_lesson(
    event: dict[str, Any], sources: dict[str, str], kind_names: dict[str, str]
) -> PlanLesson | None:
    """Zajęcia ze zdarzenia kalendarza planu; None dla zdarzeń, które nie są zajęciami
    (techniczne zdarzenie gcalsync, całodniowe, odwołane)."""
    if event.get("status") == "cancelled" or _private(event).get("gcalsync_state") == "1":
        return None
    if "dateTime" not in event.get("start", {}) or "dateTime" not in event.get("end", {}):
        return None
    start = _parse_time(event["start"]).astimezone(WARSAW)
    end = _parse_time(event["end"]).astimezone(WARSAW)
    info = _description(event.get("description", ""))
    summary = " ".join(event.get("summary", "").split())
    title = TITLE.match(summary)
    # tytuł wygrywa z opisem — mógł zostać zmieniony ręcznie
    course = title.group(1) if title else summary or info.get("course", "")
    kind = title.group(2).strip() if title else info.get("kind", "")
    seq = info.get("seq", "")
    lesson = Lesson(
        course=course,
        kind=kind,
        kind_name=kind_names.get(kind, ""),
        seq=int(seq) if seq.isdigit() else 0,
        teacher=info.get("teacher", ""),
        room=" ".join(event.get("location", "").split()),
        day=start.date(),
        start=start.strftime("%H:%M"),
        end=end.strftime("%H:%M") if end.date() == start.date() else "23:59",
    )
    source = sources.get(info.get("source", ""))
    return PlanLesson(lesson, (source,) if source else (), manual_changes(event))


def calendar_plan(
    events: Iterable[dict[str, Any]], sources: dict[str, str], kind_names: dict[str, str]
) -> list[PlanLesson]:
    """Plan grupy z kalendarza: `sources` mapuje nazwę źródła z opisu (np. „Grupa
    kierunkowa”) na kod grupy."""
    lessons = [p for e in events if (p := event_lesson(e, sources, kind_names))]
    return sorted(lessons, key=lambda p: (p.lesson.day, p.lesson.start, p.lesson.course))
