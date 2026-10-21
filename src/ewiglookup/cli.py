"""ewiglookup fetch | demo — zapis data.json dla strony z planami prowadzących i sal."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ewiglookup.build import Config, check, collect, summary, to_data
from ewiglookup.ewig import EwigClient, EwigError

DEFAULT_OUT = Path("site/data.json")


def _write(data: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    out.write_text(text, encoding="utf-8")
    print(f"Zapisano {out} ({len(text) // 1024} kB, {len(data['lessons'])} zajęć).")


def cmd_fetch(args: argparse.Namespace) -> int:
    config = Config.load(args.config)
    notes: list[str] = []
    try:
        client = EwigClient(os.environ.get("EWIG_LOGIN", ""), os.environ.get("EWIG_PASSWORD", ""))
        notes = client.notes
        collected = collect(client, config, save_dir=args.save_dir)
        check(collected, config)
    except EwigError as exc:
        print(f"Błąd: {exc}", file=sys.stderr)
        for note in notes:
            print(f"  {note}", file=sys.stderr)
        if args.summary:
            with open(args.summary, "a", encoding="utf-8") as f:
                f.write(f"## ❌ Plany nie zostały zaktualizowane\n\n{exc}\n")
        return 1
    data = to_data(collected, config.semester_iid)
    _write(data, args.out)
    report = summary(collected, data, client.notes)
    print(report)
    if collected.diagnostics:
        print("### Diagnostyka siatki\n")
        print("\n".join(f"- {line}" for line in collected.diagnostics))
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(report)
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from ewiglookup.demo import demo_collected

    _write(to_data(demo_collected(), 20261), args.out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ewiglookup", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="pobierz plany wszystkich grup z ewig (EWIG_LOGIN/…)")
    fetch.add_argument("--config", type=Path, default=Path("ewiglookup.config.json"))
    fetch.add_argument("--out", type=Path, default=DEFAULT_OUT)
    fetch.add_argument("--summary", help="dopisz podsumowanie (Markdown) do pliku")
    fetch.add_argument(
        "--save-dir", type=Path, help="zapisz pobrane strony HTML (diagnostyka, tylko lokalnie)"
    )
    fetch.set_defaults(func=cmd_fetch)

    demo = sub.add_parser("demo", help="zmyślone dane do podglądu strony")
    demo.add_argument("--out", type=Path, default=DEFAULT_OUT)
    demo.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
