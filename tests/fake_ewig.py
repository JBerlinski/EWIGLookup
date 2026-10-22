"""Atrapa serwera ewig dla requests (bez sieci) — na wzór atrapy z gcalsync.

Strony są uproszczone, ale mają te same elementy, na których opiera się klient (formularz
logowania z `index.php?sid=…`, `var sid = new String('…')`, „Zalogowany student”, lista grup
w `<select>`, siatka planu z komórkami „Przedmiot - Prowadzący (Forma)”), bez danych osobowych.
"""

from __future__ import annotations

from datetime import date, timedelta
from html import escape
from urllib.parse import parse_qs, urlparse

import requests
from requests.adapters import BaseAdapter

from ewiglookup.grid import Lesson

SID = "0123456789abcdef0123456789abcdef"

LOGIN_PAGE = f"""<html><body>
<form name=aaa border="0" class="formChaLog" method=post action=index.php?sid={SID}>
<input type="hidden" name="formname" value="login">
<input TABINDEX=1 name="userid" class="inputChaLog">
<input type="password" TABINDEX=2 name="password" class="inputChaLog">
<input type="hidden" name="view_height" VALUE="1">
</form></body></html>"""

LOGGED_PAGE = f"""<html><head><script language="JavaScript">
        var sid = new String('{SID}');
</script></head><body><i>Zalogowany student &nbsp;: </i> <b>Student Testowy</b></body></html>"""


def menu_page(groups: list[str]) -> str:
    options = "".join(f'<option value="{g}">{g}</option>' for g in groups)
    return (
        f"<html><body><script>var sid = new String('{SID}');</script>"
        f'<select name="semestr"><option value="20261">2026/2027 zimowy</option></select>'
        f'<select name="Data1">{options}</select></body></html>'
    )


def plan_cell(lesson: Lesson) -> str:
    """Komórka planu o takiej budowie jak na prawdziwej stronie ewig (zmyślone dane)."""
    title = escape(f"{lesson.course} - {lesson.teacher} ({lesson.kind_name})")
    return (
        f'<td class="tdFormList1DSheTeaGrpHTM3" valign="top" title="{title}"><center>'
        f'<table border="0" align="center" cellspacing="0" cellpadding="0" title="{title}">'
        f'<tbody><tr border="0"><td class="tdFormList1DSheTeaGrpHTM4" title="{title}"><nobr>'
        f'<b style="font-size:7.0pt;">ABC</b><br>(<b style="font-size:7.0pt;">{lesson.kind}</b>)'
        f"<br>{escape(lesson.room)}</nobr></td></tr>"
        f'<tr border="0"><td class="tdFormList1DSheTeaGrpHTM4" title="{title}"><nobr>'
        f'<a class="GlubN" title="{title}" href="javascript:cpg(&#39;n&#39;, &#39;1&#39;);">TeT'
        f"</a></nobr></td></tr>"
        f'<tr border="0"><td class="tdFormList1DSheTeaGrpHTM4" title="{title}">'
        f"<nobr>[{lesson.seq}]</nobr></td></tr></tbody></table></center></td>"
    )


ROMAN_MONTHS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
WEEKDAYS = ["PONIEDZIAŁEK", "WTOREK", "ŚRODA", "CZWARTEK", "PIĄTEK", "SOBOTA", "NIEDZIELA"]
SLOTS = [("08:00", "09:35"), ("09:50", "11:25"), ("11:40", "13:15"), ("13:30", "15:05"),
         ("15:45", "17:20"), ("17:35", "19:10"), ("19:25", "21:00")]  # fmt: skip
EMPTY = '<td class="tdFormList1DSheTeaGrpHTM3" style="">&nbsp;</td>'


def plan_grid(lessons: list[Lesson]) -> str:
    """Siatka planu: bloki dni tygodnia, wiersz dat, wiersze bloków i komórka na datę."""
    if not lessons:
        return ""
    first, last = min(e.day for e in lessons), max(e.day for e in lessons)
    monday = first - timedelta(days=first.weekday())
    weeks = (last - monday).days // 7 + 1
    by_slot: dict[tuple[date, str], list[str]] = {}
    for lesson in lessons:
        by_slot.setdefault((lesson.day, lesson.start), []).append(plan_cell(lesson))
    rows = []
    for weekday, name in enumerate(WEEKDAYS):
        dates = [monday + timedelta(days=7 * w + weekday) for w in range(weeks)]
        # Jak na prawdziwej stronie: daty poniedziałków są w wierszu nagłówka tabeli (<th>,
        # klasy thFormList1H…), pozostałych dni w komórkach tdFormList1DD….
        tag, kind = ("th", "thFormList1H") if weekday == 0 else ("td", "tdFormList1DD")
        header = "".join(
            f'<{tag} class="{kind}SheTeaGrpHTM3" valign="middle">'
            f"<nobr>{d.day:02d}<br>{ROMAN_MONTHS[d.month - 1]}</nobr></{tag}>"
            for d in dates
        )
        rows.append(
            f'<tr><{tag} valign="middle" class="{kind}SheTeaGrpHTM0" rowspan="8">'
            f"<nobr>{'<br>'.join(name)}</nobr></{tag}>"
            f'<{tag} class="{kind}SheTeaGrpHTM1">nr.<br>i</{tag}>'
            f'<{tag} class="{kind}SheTeaGrpHTM2">&nbsp;bloku<br>&nbsp;czas</{tag}>'
            f"{header}</tr>"
        )
        for number, (start, end) in enumerate(SLOTS, 1):
            cells = "".join("".join(by_slot.get((d, start), [])) or EMPTY for d in dates)
            rows.append(
                f'<tr><td class="tdFormList1DSheTeaGrpHTM1"><nobr>{number}</nobr></td>'
                f'<td class="tdFormList1DSheTeaGrpHTM1"><nobr>{start}<br>{end}</nobr></td>'
                f"{cells}</tr>"
            )
    return f'<table class="tableFormList1SheTeaGrpHTM" border="1">{"".join(rows)}</table>'


def plan_page(group: str, lessons: list[Lesson]) -> str:
    return (
        f"<html><body><script>var sid = new String('{SID}');</script>"
        f"<div>{group} (2026-09-23)</div>{plan_grid(lessons)}</body></html>"
    )


class FakeEwig(BaseAdapter):
    """Adapter podpinany pod requests.Session: session.mount('https://', FakeEwig(...))."""

    def __init__(self, plans: dict[str, list[Lesson]], password: str = "sekret"):
        super().__init__()
        self.plans = plans
        self.password = password
        self.logged_in = False
        self.logins = 0
        self.log: list[tuple[str, str, dict]] = []
        self.failures: dict[str, list[int]] = {}  # grupa -> kolejne kody HTTP przed sukcesem
        self.broken: set[str] = set()  # grupy, których plan się nie otwiera
        self.maintenance = False  # strona przerwy technicznej serwisu WAT zamiast planów

    def _response(self, request, status: int, body: bytes) -> requests.Response:
        response = requests.Response()
        response.status_code = status
        response._content = body
        response.url = request.url
        response.request = request
        response.headers["Content-Type"] = "text/html; charset=iso-8859-2"
        return response

    @staticmethod
    def _checksum_ok(query: dict) -> bool:
        """Serwer odrzuca (403) nawigację, której vrf=!<suma> nie zgadza się z checkurl()."""
        total = sum(
            j + int(ch)
            for name in ("mid", "iid", "exv")
            for value in query.get(name, [])
            for j, ch in enumerate(value)
            if ch.isdigit()
        )
        return f"!{total + 0x45}" in query.get("vrf", [])

    def send(self, request, **kwargs):
        url = urlparse(request.url)
        query = parse_qs(url.query)
        body = request.body or ""
        form = parse_qs(body if isinstance(body, str) else body.decode("latin-1"))
        self.log.append((request.method, url.path, query | form))

        if url.path == "/ed2/" and request.method == "GET":
            page = LOGIN_PAGE
        elif url.path == "/ed2/index.php" and request.method == "POST":
            ok = form.get("password") == [self.password] and form.get("formname") == ["login"]
            self.logged_in = ok
            self.logins += ok
            page = LOGGED_PAGE if ok else LOGIN_PAGE
        elif url.path == "/ed2/index.php" and query.get("lou") == ["1"]:
            self.logged_in = False
            page = LOGIN_PAGE
        elif url.path == "/ed2/logged.php" and self.maintenance and "exv" in query:
            body = "<html>Zapraszamy wkrótce SERWIS CENTRALNY WAT Chwilowa niedostępność</html>"
            return self._response(request, 200, body.encode("utf-8"))
        elif url.path == "/ed2/logged.php":
            if not self.logged_in or query.get("sid") != [SID]:
                page = LOGIN_PAGE
            elif not self._checksum_ok(query):
                return self._response(request, 403, b"<html>Forbidden</html>")
            elif "exv" not in query:
                page = menu_page(list(self.plans) + sorted(self.broken))
            else:
                group = query["exv"][0]
                if self.failures.get(group):
                    return self._response(request, self.failures[group].pop(0), b"")
                if group in self.broken:
                    page = "<html><body>Brak danych</body></html>"
                else:
                    page = plan_page(group, self.plans.get(group, []))
        else:
            return self._response(request, 404, b"not found")
        return self._response(request, 200, page.encode("iso-8859-2"))

    def close(self):
        pass
