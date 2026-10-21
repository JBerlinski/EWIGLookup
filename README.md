# EWIGLookup

Strona z planami zajęć **prowadzących** i **sal** WIG WAT. Wysyłasz link, a odbiorca wybiera
prowadzącego albo salę z listy (wyszukiwanie bez polskich znaków, np. „zmyslony”) i przegląda
plan tydzień po tygodniu albo jako listę wszystkich terminów. Adres strony zmienia się razem
z wyborem (`#t=<prowadzący>`, `#r=<sala>`), więc można też wysłać link prosto do konkretnego planu.
Kliknięcie sali w planie prowadzącego otwiera plan tej sali (i odwrotnie).

Zakładka **Wolny termin**: wybierasz prowadzącego i/lub salę, dzień i blok godzinowy, a strona od razu
mówi, czy termin jest wolny. Jeśli nie, pokazuje zajęcia, które go zajmują, i dwa najbliższe wolne
terminy (wolny prowadzący i sala jednocześnie; bez przeszłości, weekendów — chyba że zaznaczysz —
i dni, w których na WIG nie ma żadnych zajęć, np. świąt). Bloki godzinowe są brane z planów.
Kliknięcie propozycji ją sprawdza; link do wyniku można wysłać dalej.

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
   `EWIG_PASSWORD` (te same co w gcalsync).
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
