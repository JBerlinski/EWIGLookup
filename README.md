# EWIGLookup

Strona z planami zajęć **prowadzących** i **sal** WIG WAT. Wysyłasz link, a odbiorca wybiera
prowadzącego albo salę z listy (wyszukiwanie bez polskich znaków, np. „zmyslony”) i przegląda
plan tydzień po tygodniu albo jako listę wszystkich terminów. Adres strony zmienia się razem
z wyborem (`#t=<prowadzący>`, `#r=<sala>`), więc można też wysłać link prosto do konkretnego planu.
Kliknięcie sali w planie prowadzącego otwiera plan tej sali (i odwrotnie).

Zakładki **WIG23IX2S1**, **WIG23IX1S1** (po jednej na każdy plan z gcalsync): plan naszej grupy
z prowadzącym i salą przy każdych zajęciach (`#g=<plan>`).

Zakładka **Przełożenie zajęć**: czy zajęcia z danym prowadzącym można przełożyć na dany termin
do danej sali tak, żeby pasowało naszym grupom. Wybierasz prowadzącego, salę, grupy (plany
złożone jak w gcalsync — domyślnie wszystkie), dzień i blok godzinowy. Termin pasuje, gdy wolni są
jednocześnie prowadzący, sala i grupy; jeśli nie, strona pokazuje kolizje (kogo zajmują) i dwa
najbliższe pasujące terminy (bez przeszłości, weekendów — chyba że zaznaczysz — i dni bez zajęć
na WIG, np. świąt). Pod spodem siatka całego tygodnia: zajętość sali, prowadzącego i grup razem,
wolny blok można kliknąć. Link do wyniku można wysłać dalej.

### Plany naszych grup = Kalendarz Google

Plany naszych grup edytuje się wyłącznie w Kalendarzu Google (kalendarze gcalsync), także przez
osoby, którym kalendarz udostępniono. Dlatego automat czyta je **z kalendarzy** (lista planów
i ID kalendarzy z `gcalsync.config.json` w repozytorium
[gcalsync](https://github.com/JBerlinski/Gcalsync)): przeniesienia, zmiany sal, usunięte i dodane
zajęcia są na stronie tak jak w kalendarzu. Zajęcia zmienione ręcznie mają dopisek
(„zmieniono ręcznie: czas, sala”, „dodane ręcznie”) — rozpoznawane tak jak w gcalsync, po skrótach
pól zapisanych w zdarzeniu. Prowadzący i sala są brane z opisu i lokalizacji zdarzenia.

Plany prowadzących i sal zostają z e-Dziekanatu (ręczne usunięcie w kalendarzu grupy zwykle znaczy
„nie chodzimy”, nie „zajęcia odwołane”).

Do odczytu potrzebny jest sekret `GOOGLE_TOKEN_JSON` — ten sam token co w gcalsync (zawartość
`%LOCALAPPDATA%\gcalsync\token.json`; tylko odczyt kalendarzy gcalsync). Bez niego (albo gdy
Google odmówi) plany grup są składane z e-Dziekanatu tak jak w gcalsync — bez ręcznych zmian —
a strona to zaznacza.

## Skąd są dane

ewig nie udostępnia planów bez logowania, a eksport CSV nie zawiera prowadzących. Dlatego
(tak jak [gcalsync](https://github.com/JBerlinski/Gcalsync)) automat loguje się do ewig, otwiera
„Rozkład zajęć grupy” i pobiera **stronę planu każdej grupy z listy**. W siatce planu każda komórka
ma przedmiot, prowadzącego, formę zajęć i salę — z nich składane są plany prowadzących i sal.
Te same zajęcia w planach kilku grup (np. wykład) są łączone w jedną pozycję z listą grup.

Konsekwencja: plan prowadzącego/sali zawiera tylko zajęcia **grup widocznych na liście w ewig**
(np. bez zajęć prowadzącego na innym wydziale).

## Automatyzacja (GitHub Actions + GitHub Pages)

`.github/workflows/pages.yml` około 5:00 i 17:00 (oraz ręcznie: Actions → „Aktualizacja planów”
→ „Run workflow”) pobiera plany, zapisuje `site/data.json` i publikuje katalog `site/` na GitHub
Pages. Dane nie są commitowane do repozytorium — istnieją tylko w opublikowanej stronie.

Strona **nie jest aktualizowana** (zostaje poprzednia wersja, a GitHub wysyła e-mail o błędzie),
gdy ewig nie działa, logowanie się nie udało albo nie pobrano ponad 20% grup. Pojedyncze nieudane
grupy są wypisane w podsumowaniu uruchomienia i w ostrzeżeniu na stronie.

### Jednorazowa konfiguracja

1. **Settings → Secrets and variables → Actions → New repository secret:** `EWIG_LOGIN`,
   `EWIG_PASSWORD`, `GOOGLE_TOKEN_JSON` (te same co w gcalsync).
2. **Settings → Pages → Build and deployment → Source: „GitHub Actions”.**
   GitHub Pages w repozytorium prywatnym wymaga GitHub Pro; na darmowym koncie repozytorium musi
   być publiczne (publiczny jest wtedy tylko kod — dane są wyłącznie na stronie).
3. Workflow musi być na gałęzi domyślnej (`main`) — harmonogram działa tylko stamtąd.
4. Actions → „Aktualizacja planów” → „Run workflow”. Adres strony jest w podsumowaniu
   zadania `deploy` (zwykle `https://<użytkownik>.github.io/EWIGLookup/`).

Strona ma `noindex` i `robots.txt` blokujące wyszukiwarki, ale każdy, kto ma link, ją zobaczy.

### Konfiguracja — `ewiglookup.config.json`

| pole | znaczenie |
|---|---|
| `semester_iid` | semestr w ewig, np. `20261` = 2026/2027 zimowy, `20262` = letni |
| `group_pattern` | wyrażenie regularne kodów grup z listy (odsiewa inne pola wyboru) |
| `include` / `exclude` | grupy dopisane ręcznie / pomijane |
| `max_failed_share` | jaki ułamek nieudanych grup jeszcze pozwala opublikować stronę |
| `gcalsync_config` | adres albo ścieżka `gcalsync.config.json` z planami grup; `""` — bez planów |

## Lokalnie

Wymaga [uv](https://docs.astral.sh/uv/).

```
uv run ewiglookup demo                      # zmyślone dane w site/data.json
python -m http.server -d site 8000          # podgląd: http://localhost:8000
EWIG_LOGIN=… EWIG_PASSWORD=… uv run ewiglookup fetch --save-dir raw   # prawdziwe dane
uv run pytest && uv run ruff check .
```

`--save-dir` zapisuje pobrane strony HTML (do diagnozy, gdy ewig zmieni wygląd strony). Strona menu
zawiera imię i nazwisko zalogowanego — nie commituj tego katalogu (`raw/` jest w `.gitignore`).
