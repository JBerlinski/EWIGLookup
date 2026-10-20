"""Klient e-Dziekanatu WIG (ewig.wcy.wat.edu.pl/ed2) — na wzór gcalsync (sources/ewig.py).

Robi to samo co przeglądarka:
1. GET strony logowania — identyfikator sesji `sid` jest w adresie formularza,
2. POST formularza logowania (`formname=login`, `default_fun=1` = „Aktualności”),
3. GET pozycji menu „Rozkład zajęć grupy” (`mid=328`) — lista grup w `<select>`,
4. GET planu każdej grupy (`exv=<kod grupy>`) — bez eksportu CSV: siatka na stronie ma
   prowadzących i sale, a eksport ich nie ma (i bywał zepsuty),
5. wylogowanie (`index.php?sid=…&lou=1`).

Hasło nie jest nigdzie logowane ani umieszczane w komunikatach błędów.
"""

from __future__ import annotations

import html
import re
import time
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import urlencode, urlparse

import requests

BASE_URL = "https://ewig.wcy.wat.edu.pl/ed2/"
PAGE_ENCODING = "iso-8859-2"
MID_GROUP_PLAN = 328
# (połączenie, odczyt) w sekundach; strona planu grupy ma ~300 kB i bywa generowana wolno.
TIMEOUT_SECONDS = (15, 90)
RETRY_DELAYS = (5, 20)  # przerwy między próbami tego samego zapytania (błąd sieci lub 5xx)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0 Safari/537.36 ewiglookup/0.1"
)

SID_IN_PAGE = re.compile(r"var sid = new String\('([0-9a-fA-F]+)'\)")
OPTION = re.compile(r"""<option\b[^>]*?\bvalue\s*=\s*(["']?)([^"'\s>]*)\1""", re.I)
SHOW_GROUP = re.compile(r"""showGroupPlan\(\s*(["'])([^"']+)\1""", re.I)


class EwigError(Exception):
    pass


class EwigLoginError(EwigError):
    pass


class EwigConnectionError(EwigError):
    """ewig nie odpowiada (timeout, zerwane połączenie, błąd 5xx) mimo ponowień."""


class EwigSessionError(EwigError):
    """ewig odrzucił zapytanie (HTTP 401/403) — zwykle wygasła lub unieważniona sesja."""


# --- adresy (odtworzone w gcalsync z menubody.js / form.js / shedule_teacher_group.js) -----

# checkurl() najpierw woła prolongTimeOut(), która ustawia zmienną strony `used` na 0x45.
USED_AFTER_PROLONG = 0x45


def checksum(*values: str, used: int = USED_AFTER_PROLONG) -> int:
    """Suma `vrf` z checkurl(): dla każdej cyfry na pozycji j dodaje j+cyfra, na końcu used."""
    total = 0
    for value in values:
        for j, ch in enumerate(value):
            if ch.isdigit():
                total += j + int(ch)
    return total + used


def menu_url(sid: str, semester_iid: int) -> str:
    """Pozycja menu executeCmm(328, <semestr>, 1, ''): makeURL() + checkurl()."""
    ss = checksum(str(MID_GROUP_PLAN), str(semester_iid))
    query = [("sid", sid), ("mid", str(MID_GROUP_PLAN)), ("iid", str(semester_iid))]
    query += [("vrf", f"!{ss}"), ("rdo", "1"), ("pos", "0")]
    return BASE_URL + "logged.php?" + urlencode(query, safe="!")


def group_plan_url(sid: str, semester_iid: int, group: str) -> str:
    """showGroupPlan(grupa): getURL() + exv=<grupa>, a potem checkurl() dopisuje vrf=!<suma>."""
    mid, iid = str(MID_GROUP_PLAN), str(semester_iid)
    query = [("sid", sid), ("mid", mid), ("iid", iid), ("vrf", f"{mid}{iid}")]
    query += [("rdo", "1"), ("pos", "0"), ("exv", group)]
    query += [("vrf", f"!{checksum(mid, iid, group)}"), ("rdo", "1"), ("pos", "0")]
    return BASE_URL + "logged.php?" + urlencode(query, safe="!")


def parse_groups(menu_page: str, pattern: str) -> list[str]:
    """Kody grup z listy wyboru na stronie „Rozkład zajęć grupy” (bez powtórzeń, w kolejności).

    `pattern` odsiewa inne pola wyboru (np. semestr).
    """
    accept = re.compile(pattern)
    found = [html.unescape(m.group(2)).strip() for m in OPTION.finditer(menu_page)]
    found += [html.unescape(m.group(2)).strip() for m in SHOW_GROUP.finditer(menu_page)]
    return list(dict.fromkeys(g for g in found if accept.fullmatch(g)))


class _LoginFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_form = False
        self.action: str | None = None
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "form" and "index.php?sid=" in a.get("action", ""):
            self.in_form, self.action = True, a["action"]
        elif tag == "input" and self.in_form and a.get("type", "text").lower() == "hidden":
            self.fields[a.get("name", "")] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_form = False


def page_hint(page: str, limit: int = 200) -> str:
    """Początek widocznego tekstu strony — do diagnozy w logu (bez danych zalogowanego)."""
    text = re.sub(r"<(script|style)\b.*?</\1\s*>", " ", page, flags=re.S | re.I)
    text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
    if "Zalogowany" in text:  # strona po zalogowaniu zawiera imię i nazwisko — nie logujemy
        return "(strona zalogowanego użytkownika)"
    return (text[:limit] + "…") if len(text) > limit else (text or "(pusta)")


class EwigClient:
    def __init__(
        self,
        login: str,
        password: str,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not login or not password:
            raise EwigLoginError("Brak loginu lub hasła do ewig (EWIG_LOGIN / EWIG_PASSWORD).")
        self._login = login
        self._password = password
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self._sleep = sleep
        self.sid: str | None = None
        self.notes: list[str] = []  # przebieg ponowień — do logu uruchomienia
        self._referer: str | None = None

    def _request(
        self, method: str, url: str, step: str, *, retry: bool = True, **kwargs
    ) -> requests.Response:
        """Zapytanie jak z przeglądarki: Referer poprzedniej strony, ponowienia z przerwami."""
        headers = {"Referer": self._referer} if self._referer else {}
        if method == "POST":
            headers["Origin"] = BASE_URL.rstrip("/").rsplit("/", 1)[0]
        where = f"{step} ({urlparse(url).path})"  # bez parametrów: zawierają identyfikator sesji
        last = ""
        delays = RETRY_DELAYS if retry else ()
        for attempt in range(len(delays) + 1):
            try:
                response = self.session.request(
                    method, url, headers=headers, timeout=TIMEOUT_SECONDS, **kwargs
                )
            except requests.RequestException as exc:
                last = f"{type(exc).__name__}: {exc}"
            else:
                if response.status_code < 500:
                    if response.status_code >= 400:
                        error = (
                            EwigSessionError if response.status_code in (401, 403) else EwigError
                        )
                        raise error(f"ewig zwrócił HTTP {response.status_code} — krok: {where}.")
                    self._referer = response.url or url
                    return response
                last = f"HTTP {response.status_code}"
            if attempt < len(delays):
                self.notes.append(f"ponawiam za {delays[attempt]} s — {where}: {last}")
                self._sleep(delays[attempt])
        raise EwigConnectionError(f"Brak połączenia z ewig — krok: {where}: {last}")

    def pause(self, seconds: float) -> None:
        self._sleep(seconds)

    @staticmethod
    def _text(response: requests.Response) -> str:
        return response.content.decode(PAGE_ENCODING, errors="replace")

    def login(self) -> str:
        self.session.cookies.clear()
        self._referer = None
        page = self._text(self._request("GET", BASE_URL, "strona logowania"))
        form = _LoginFormParser()
        form.feed(page)
        if not form.action:
            raise EwigError("Nie znaleziono formularza logowania — strona ewig się zmieniła.")
        data = {**form.fields, "formname": "login", "default_fun": "1"}
        data.update(userid=self._login, password=self._password)
        response = self._request(
            "POST",
            BASE_URL + form.action,
            "logowanie",
            data={k: v.encode(PAGE_ENCODING) for k, v in data.items()},
        )
        text = self._text(response)
        match = SID_IN_PAGE.search(text)
        if "Zalogowany" not in text or not match:
            if 'name="userid"' in text or "name=userid" in text:
                raise EwigLoginError("Logowanie do ewig nie powiodło się — sprawdź login i hasło.")
            raise EwigSessionError(
                "Nieoczekiwana odpowiedź po logowaniu (ewig przeciążony albo strona się zmieniła)"
                f" — treść strony: „{page_hint(text)}”."
            )
        self.sid = match.group(1)
        return self.sid

    def _require_sid(self) -> str:
        if self.sid is None:
            raise EwigError("Najpierw zaloguj się (login()).")
        return self.sid

    def menu_page(self, semester_iid: int) -> str:
        """Strona „Rozkład zajęć grupy” z listą grup (także wejście w menu przed planem grupy)."""
        url = menu_url(self._require_sid(), semester_iid)
        return self._text(self._request("GET", url, "menu planu grup"))

    def group_page(self, semester_iid: int, group: str) -> str:
        url = group_plan_url(self._require_sid(), semester_iid, group)
        page = self._text(self._request("GET", url, f"plan {group}"))
        if group not in page:
            if SID_IN_PAGE.search(page) is None and "userid" in page:
                raise EwigSessionError(f"Sesja ewig wygasła przy planie grupy {group}.")
            raise EwigError(f"Nie udało się otworzyć planu grupy {group}: „{page_hint(page)}”.")
        return page

    def logout(self) -> None:
        if self.sid is None:
            return
        try:
            self.session.get(f"{BASE_URL}index.php?sid={self.sid}&lou=1", timeout=TIMEOUT_SECONDS)
        except requests.RequestException:
            pass  # wylogowanie jest uprzejmością; sesja i tak wygaśnie
        finally:
            self.sid = None
