from __future__ import annotations

import json
from datetime import date
from urllib.parse import parse_qsl, urlparse

import pytest
import requests

from ewiglookup.build import Collected, Config, check, collect, merge, to_data
from ewiglookup.cli import main
from ewiglookup.ewig import (
    EwigClient,
    EwigConnectionError,
    EwigError,
    EwigLoginError,
    group_plan_url,
    menu_url,
    parse_groups,
)
from ewiglookup.grid import Lesson, count_cells, diagnose, parse_cell, parse_plan_grid
from tests.fake_ewig import FakeEwig, plan_page

T1 = "dr inż. Jan Testowy"
T2 = "mgr Anna Przykładowa"
LECTURE = Lesson("Geodezja", "w", "Wykład", 1, T1, "18 58", date(2026, 10, 1), "08:00", "09:35")
PLANS = {
    "WIG23IX1S1": [
        LECTURE,
        Lesson("Fotogrametria", "L", "Laboratorium", 1, T2, "57 12", date(2026, 10, 1),
               "09:50", "11:25"),
        Lesson("Geodezja", "ć", "Ćwiczenia", 1, T1, "14 3", date(2027, 1, 12), "13:30", "15:05"),
    ],
    "WIG23IX2S1": [
        LECTURE,
        Lesson("Fotogrametria", "L", "Laboratorium", 1, T2, "57 12", date(2026, 10, 1),
               "11:40", "13:15"),
    ],
}  # fmt: skip
CONFIG = Config(semester_iid=20261)


def client(fake: FakeEwig, password: str = "sekret") -> EwigClient:
    session = requests.Session()
    session.mount("https://", fake)
    return EwigClient("student", password, session=session, sleep=lambda _s: None)


# --- adresy (te same co w gcalsync) --------------------------------------------------------


def test_menu_url():
    params = parse_qsl(urlparse(menu_url("abc", 20261)).query)
    assert params == [("sid", "abc"), ("mid", "328"), ("iid", "20261"), ("vrf", "!106"),
                      ("rdo", "1"), ("pos", "0")]  # fmt: skip


def test_group_plan_url():
    params = parse_qsl(urlparse(group_plan_url("abc", 20261, "WIG23IX2S1")).query)
    assert params == [
        ("sid", "abc"), ("mid", "328"), ("iid", "20261"), ("vrf", "32820261"), ("rdo", "1"),
        ("pos", "0"), ("exv", "WIG23IX2S1"), ("vrf", "!137"), ("rdo", "1"), ("pos", "0"),
    ]  # fmt: skip


# --- strony ---------------------------------------------------------------------------------


def test_parse_groups_skips_other_selects_and_duplicates():
    page = (
        '<select name="s"><option value="20261">zima</option></select>'
        "<select name=Data1><option value=WIG23IX1S1>a<option value='WIG24GX2S1'>b"
        '<option value="WIG23IX1S1">c<option value="">--</select>'
        "<a href=\"javascript:showGroupPlan('WIG25IX1N1')\">x</a>"
    )
    assert parse_groups(page, CONFIG.group_pattern) == ["WIG23IX1S1", "WIG24GX2S1", "WIG25IX1N1"]


def test_parse_cell_reads_teacher_room_and_kind():
    from tests.fake_ewig import plan_cell

    cell = plan_cell(Lesson("Bazy danych - SQL", "L", "Laboratorium", 7, T2, "100 5",
                            date(2026, 10, 1), "08:00", "09:35"))  # fmt: skip
    assert parse_cell(cell) == ("Bazy danych - SQL", "L", "Laboratorium", 7, T2, "100 5")


def test_parse_cell_without_teacher():
    cell = ('<table title="Wychowanie fizyczne (Ćwiczenia)"><tr><td><nobr>(<b>ć</b>)<br>Hala'
            "</nobr></td></tr><tr><td><nobr>[3]</nobr></td></tr></table>")  # fmt: skip
    assert parse_cell(cell) == ("Wychowanie fizyczne", "ć", "Ćwiczenia", 3, "", "Hala")


def test_grid_round_trip_with_year_change():
    page = plan_page("WIG23IX1S1", PLANS["WIG23IX1S1"])
    lessons = parse_plan_grid(page, 20261)
    assert lessons == sorted(PLANS["WIG23IX1S1"], key=lambda e: (e.day, e.start))
    assert count_cells(page) == len(lessons)


# --- pobieranie -----------------------------------------------------------------------------


def test_collect_reads_all_groups_in_one_session():
    fake = FakeEwig(PLANS)
    collected = collect(client(fake), CONFIG)
    assert list(collected.groups) == ["WIG23IX1S1", "WIG23IX2S1"]
    assert collected.groups["WIG23IX2S1"][0].teacher == T1
    assert fake.logins == 1
    assert not fake.logged_in  # wylogowanie na końcu
    assert not collected.failed and not collected.warnings


def test_collect_include_and_exclude():
    fake = FakeEwig(PLANS)
    config = Config(semester_iid=20261, exclude=("WIG23IX2S1",))
    assert list(collect(client(fake), config).groups) == ["WIG23IX1S1"]


def test_broken_group_is_skipped_and_reported():
    fake = FakeEwig(PLANS)
    fake.broken.add("WIG99XX1S1")
    collected = collect(client(fake), CONFIG)
    assert list(collected.groups) == ["WIG23IX1S1", "WIG23IX2S1"]
    assert "WIG99XX1S1" in collected.failed


def test_forbidden_group_is_retried_in_new_session():
    fake = FakeEwig(PLANS)
    fake.failures["WIG23IX2S1"] = [403]
    c = client(fake)
    collected = collect(c, CONFIG)
    assert list(collected.groups) == ["WIG23IX1S1", "WIG23IX2S1"]
    assert fake.logins == 2
    assert any("nowej sesji" in n for n in c.notes)


def test_ewig_down_aborts_whole_run():
    fake = FakeEwig(PLANS)
    fake.failures["WIG23IX1S1"] = [503] * 6
    with pytest.raises(EwigConnectionError):
        collect(client(fake), CONFIG)
    assert not fake.logged_in


def test_wrong_password():
    with pytest.raises(EwigLoginError, match="login i hasło"):
        collect(client(FakeEwig(PLANS), password="zle"), CONFIG)


def test_no_groups_on_menu_is_error():
    with pytest.raises(EwigError, match="nie znaleziono żadnej grupy"):
        collect(client(FakeEwig({})), CONFIG)


# --- dane dla strony -------------------------------------------------------------------------


def test_merge_joins_same_lesson_of_several_groups():
    merged = merge(PLANS)
    lecture = [groups for lesson, groups in merged if lesson == LECTURE]
    assert lecture == [["WIG23IX1S1", "WIG23IX2S1"]]
    assert len(merged) == 4


def test_to_data_is_compact_and_indexed():
    data = to_data(Collected(groups=PLANS), 20261)
    assert data["teachers"] == sorted([T1, T2])
    assert data["rooms"] == ["14 3", "18 58", "57 12"]
    assert data["kinds"] == {"L": "Laboratorium", "w": "Wykład", "ć": "Ćwiczenia"}
    first = data["lessons"][0]
    assert first[:3] == ["2026-10-01", "08:00", "09:35"]
    assert data["courses"][first[3]] == "Geodezja"
    assert data["teachers"][first[6]] == T1 and data["rooms"][first[7]] == "18 58"
    assert [data["groups"][i] for i in first[8]] == ["WIG23IX1S1", "WIG23IX2S1"]


def test_check_rejects_too_many_failures():
    collected = Collected(groups={"A": [LECTURE]}, failed={"B": "x", "C": "y"})
    with pytest.raises(EwigError, match="Nie pobrano 2 z 3"):
        check(collected, CONFIG)
    check(Collected(groups={"A": [LECTURE]}), CONFIG)
    with pytest.raises(EwigError, match="żadnych zajęć"):
        check(Collected(groups={"A": []}), CONFIG)


def test_cli_demo_writes_data(tmp_path):
    out = tmp_path / "data.json"
    assert main(["demo", "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["lessons"] and data["teachers"] and data["rooms"]


def test_cli_fetch_without_credentials_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("EWIG_LOGIN", raising=False)
    monkeypatch.delenv("EWIG_PASSWORD", raising=False)
    config = tmp_path / "c.json"
    config.write_text('{"semester_iid": 20261}', encoding="utf-8")
    summary = tmp_path / "summary.md"
    args = ["fetch", "--config", str(config), "--out", str(tmp_path / "d.json")]
    assert main([*args, "--summary", str(summary)]) == 1
    assert "EWIG_LOGIN" in capsys.readouterr().err
    assert "nie zostały zaktualizowane" in summary.read_text(encoding="utf-8")
    assert not (tmp_path / "d.json").exists()


def test_grid_reads_every_grid_table_on_page():
    from tests.fake_ewig import plan_grid

    autumn, winter = PLANS["WIG23IX1S1"][:2], PLANS["WIG23IX1S1"][2:]
    page = f"<html><body>WIG23IX1S1{plan_grid(autumn)}<p>dalej</p>{plan_grid(winter)}</body></html>"
    assert len(parse_plan_grid(page, 20261)) == 3
    assert count_cells(page) == 3
    assert diagnose(page, 20261)[0].startswith("tabel siatki: 2;")


def test_diagnose_names_unread_cells():
    page = plan_page("WIG23IX1S1", PLANS["WIG23IX1S1"])
    # komórka z inną klasą (np. wyróżniony dzień) nie jest uznana za komórkę daty
    lesson_cell = 'class="tdFormList1DSheTeaGrpHTM3" valign="top"'
    page = page.replace(lesson_cell, lesson_cell.replace("HTM3", "HTM3x"), 1)
    assert len(parse_plan_grid(page, 20261)) < count_cells(page)
    lines = diagnose(page, 20261)
    assert any("komórek na" in line and "HTM3x" in line for line in lines)
    assert any(line.startswith("brak: Geodezja") and "HTM3x" in line for line in lines)


def test_maintenance_page_is_outage_not_empty_group():
    fake = FakeEwig(PLANS)
    fake.maintenance = True
    c = client(fake)
    with pytest.raises(EwigConnectionError, match="przerwa techniczna"):
        collect(c, CONFIG)
    assert fake.logins == 2  # jedna próba w nowej sesji, potem przerwanie całości
    plan_requests = [q for _, path, q in fake.log if path == "/ed2/logged.php" and "exv" in q]
    assert {q["exv"][0] for q in plan_requests} == {"WIG23IX1S1"}  # dalsze grupy nie są odpytywane
    assert not fake.logged_in


def test_monday_dates_in_table_header_row_are_read():
    # Na stronie ewig daty poniedziałków są w wierszu <th> nagłówka tabeli, nie w td…DD….
    monday = Lesson("Geodezja", "w", "Wykład", 2, T1, "18 58", date(2026, 10, 5), "08:00", "09:35")
    lessons = [monday, *PLANS["WIG23IX1S1"]]
    page = plan_page("WIG23IX1S1", lessons)
    assert '<th class="thFormList1HSheTeaGrpHTM3"' in page
    assert parse_plan_grid(page, 20261) == sorted(lessons, key=lambda e: (e.day, e.start))
    assert count_cells(page) == len(lessons)


# --- plany grup jak w gcalsync -------------------------------------------------------------

from ewiglookup.build import usable_plans  # noqa: E402
from ewiglookup.plans import Plan, Rule, load_plans, parse_plans, plan_lessons  # noqa: E402

GCALSYNC_CONFIG = {
    "version": 2,
    "policy": "priority",
    "plans": [
        {
            "name": "WIG23IX2S1",
            "groups": [
                {"code": "WIG23IX2S1", "name": "Grupa kierunkowa"},
                {"code": "WIG23IX1S1", "name": "Grupa domyślna"},
            ],
            "rules": [{"field": "course", "op": "equals", "value": "modelowanie  danych do BIM"}],
        },
        {"name": "WIG23IX1S1", "groups": [{"code": "WIG23IX1S1", "name": "Grupa"}], "rules": []},
    ],
}


def _l(course, day, start, end, kind="w", seq=1, room="1 1", teacher=T1):
    return Lesson(course, kind, "Wykład", seq, teacher, room, day, start, end)


def test_plan_like_gcalsync_rules_duplicates_and_priority():
    d = date(2026, 10, 5)
    main = [
        _l("Geodezja", d, "08:00", "09:35", seq=3, room="A"),
        _l("Fotogrametria", d, "11:40", "13:15"),
    ]
    default = [
        _l("Geodezja", d, "08:00", "09:35", seq=4, room="B"),  # duplikat (inny numer i sala)
        _l("Bazy danych", d, "11:40", "13:15"),  # koliduje z ważniejszą grupą -> odpada
        _l("Modelowanie danych do BIM", d, "13:30", "15:05"),  # wykluczone regułą
        _l("Statystyka", d, "13:15", "14:00"),  # tylko styka się z niczym ważniejszym -> zostaje
    ]
    plan = parse_plans(GCALSYNC_CONFIG)[0]
    got = plan_lessons(plan, {"WIG23IX2S1": main, "WIG23IX1S1": default})
    assert [(e.course, e.room) for e in got] == [
        ("Geodezja", "A"), ("Fotogrametria", "1 1"), ("Statystyka", "1 1"),
    ]  # fmt: skip


def test_rule_options_sources_dates_regex_subject():
    d = date(2026, 10, 5)
    lesson = _l("Geodezja wyższa", d, "08:00", "09:35", kind="L", seq=7)
    assert Rule("subject", "contains", "(L) [7]").matches(lesson, "x")
    assert Rule("course", "regex", "^geodezja").matches(lesson, "x")
    assert not Rule("course", "regex", "^geodezja", case_sensitive=True).matches(lesson, "x")
    assert not Rule("course", "contains", "geo", sources=("Grupa kierunkowa",)).matches(lesson, "x")
    assert not Rule("course", "contains", "geo", date_from=date(2026, 11, 1)).matches(lesson, "x")
    assert not Rule("course", "contains", "geo", enabled=False).matches(lesson, "x")


def test_plans_in_data_point_at_lessons(tmp_path):
    path = tmp_path / "gcalsync.config.json"
    path.write_text(json.dumps(GCALSYNC_CONFIG), encoding="utf-8")
    plans = load_plans(str(path))
    collected = Collected(groups=PLANS)
    data = to_data(collected, 20261, usable_plans(plans, collected))
    by_name = {p["name"]: p["lessons"] for p in data["plans"]}
    assert len(by_name["WIG23IX1S1"]) == 3
    # wspólny wykład jest raz; laboratoria obu grup w różnych godzinach — oba zostają
    assert len(by_name["WIG23IX2S1"]) == 4
    assert all(0 <= i < len(data["lessons"]) for ids in by_name.values() for i in ids)


def test_plan_with_missing_group_is_skipped():
    collected = Collected(groups={"WIG23IX1S1": PLANS["WIG23IX1S1"]})
    plans = usable_plans(parse_plans(GCALSYNC_CONFIG), collected)
    assert [p.name for p in plans] == ["WIG23IX1S1"]
    assert "WIG23IX2S1 pominięty" in collected.warnings[0]


def test_keep_all_policy_keeps_colliding_lessons():
    d = date(2026, 10, 5)
    plan = Plan("P", (("A", "a"), ("B", "b")), (), keep_all=True)
    groups = {"A": [_l("X", d, "08:00", "09:35")], "B": [_l("Y", d, "08:00", "09:35")]}
    assert len(plan_lessons(plan, groups)) == 2
