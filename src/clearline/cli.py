"""Command-line interface: `clearline setup | search | profile | check | serve`."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from . import __version__
from .db import DBLP, MissingDatabase
from .pclist import parse
from .profile import Affiliation, Profile, ProfileStore, Relation, parse_month
from .rules import CONFLICT, POTENTIAL, Settings, build_authors, check, summarize


def home() -> Path:
    return Path(os.environ.get("CLEARLINE_HOME", Path.home() / ".clearline")).expanduser()


def db_path() -> Path:
    return home() / "dblp.sqlite"


def store() -> ProfileStore:
    return ProfileStore(home() / "profiles")


# ---------------------------------------------------------------- setup
def cmd_setup(args) -> int:
    from .dblp_import import DUMP_URL, download, import_dump

    dump = Path(args.dump).expanduser() if args.dump else home() / "dblp.xml.gz"
    if not dump.exists():
        if args.dump:
            print(f"No file at {dump}", file=sys.stderr)
            return 1
        print(f"Downloading the DBLP dump (about 1.1 GB) from {DUMP_URL}", file=sys.stderr)
        download(dump)
    elif not args.dump and not args.reuse:
        print(f"Using the existing dump at {dump} (run `clearline refresh` to download a new one).",
              file=sys.stderr)
    print("Importing. This takes roughly 10 to 20 minutes and needs about 5 GB of disk.",
          file=sys.stderr)
    stats = import_dump(dump, db_path())
    print(f"Done: {stats['publications']:,} publications, {stats['persons']:,} people "
          f"in {stats['seconds']:,}s.\nDatabase: {db_path()}")
    if args.delete_dump and not args.dump:
        dump.unlink()
        print(f"Deleted {dump}.")
    return 0


def cmd_refresh(args) -> int:
    from .dblp_import import download

    # download() replaces the old dump only once the new one is complete and verified.
    download(home() / "dblp.xml.gz")
    args.dump, args.reuse = None, True
    return cmd_setup(args)


# --------------------------------------------------------------- search
def _show_person(p, indent="  ") -> str:
    lines = [f"{p.name}  [{p.pid or 'no DBLP page'}]  {p.n_pubs} pubs, last {p.last_year}"]
    if p.affiliations:
        lines.append(f"{indent}affiliation: {'; '.join(p.affiliations)}")
    if p.former_affiliations:
        lines.append(f"{indent}former: {'; '.join(p.former_affiliations)}")
    return "\n".join(lines)


def cmd_search(args) -> int:
    db = DBLP(db_path())
    people = db.find(" ".join(args.name))
    if not people:
        print("No match in DBLP.")
        return 1
    for p in people:
        print(_show_person(p))
    print("\nUse the id in brackets as `dblp = ...` in a profile, or with `check --author`.")
    return 0


# -------------------------------------------------------------- profile
def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def _ask_month(prompt: str, *, end: bool = False) -> str:
    while True:
        value = _ask(prompt)
        try:
            parse_month(value, end=end)
            return value
        except ValueError as e:
            print(f"  {e}")


def cmd_profile_new(args) -> int:
    print("Create a COI profile. Press Enter to skip optional questions.\n")
    name = _ask("Your name")
    dblp = ""
    try:
        db = DBLP(db_path())
        people = db.find(name)
        for i, p in enumerate(people[:9], 1):
            print(f"  {i}. {_show_person(p, '       ')}")
        if people:
            pick = _ask("Which DBLP entry is you? (number, or a DBLP pid)", "1" if len(people) == 1 else "")
            if pick.isdigit() and 1 <= int(pick) <= len(people):
                chosen = people[int(pick) - 1]
                dblp = chosen.pid or chosen.name
            else:
                dblp = pick
    except MissingDatabase:
        dblp = _ask("DBLP pid or profile URL (optional)")

    affs = []
    print("\nAffiliations, newest first. Leave the institution empty to finish.")
    while inst := _ask("  Institution"):
        domains = [d.strip() for d in _ask("  Email domains (comma separated)").split(",") if d.strip()]
        start = _ask_month("  Start month (YYYY-MM)")
        end = _ask_month("  End month (YYYY-MM, empty if current)", end=True)
        affs.append(Affiliation(inst, domains, start, end))

    rels = []
    print("\nPeople you always conflict with (advisor, students, family). Empty name to finish.")
    while rname := _ask("  Name"):
        rels.append(Relation(rname, _ask("  Kind", "advisor"), _ask("  Note")))

    profile = Profile(name=name, dblp=dblp, affiliations=affs, relations=rels)
    path = store().save(profile)
    print(f"\nSaved {path}. Edit it any time; it is plain TOML.")
    return 0


def cmd_profile_list(args) -> int:
    st = store()
    profiles = st.list()
    for path, error in st.errors:
        print(f"warning: skipped {path.name}: {error}", file=sys.stderr)
    if not profiles:
        print("No profiles yet. Run `clearline profile new` or use `clearline serve`.")
    for p in profiles:
        print(f"{p.slug}: {p.name}  (dblp: {p.dblp or '-'}, {len(p.affiliations)} affiliations)")
    return 0


def cmd_profile_show(args) -> int:
    p = store().load(args.name)
    print(f"# {store().path(p.slug)}\n{p.to_toml()}")
    return 0


# ---------------------------------------------------------------- check
def _settings(args) -> Settings:
    as_of = parse_month(args.as_of) if args.as_of else dt.date.today()
    return Settings(coauthor_years=args.coauthor_years, institution_years=args.institution_years,
                    max_authors=args.max_authors, as_of=as_of)


def cmd_check(args) -> int:
    text = sys.stdin.read() if args.pclist == "-" else Path(args.pclist).read_text(encoding="utf-8")
    members = parse(text)
    if not members:
        print("No names found in the reviewer list.", file=sys.stderr)
        return 1
    db = DBLP(db_path())
    st = store()
    profiles = [st.load_file(Path(p)) if p.endswith(".toml") else st.load(p) for p in args.profile]
    if not profiles and not args.author:
        available = st.list()
        if len(available) != 1:
            names = ", ".join(p.slug for p in available) or "none yet"
            print(f"Choose the paper's authors with --profile (profiles: {names}) "
                  "or --author.", file=sys.stderr)
            return 1
        profiles = available
    authors, warnings = build_authors(db, profiles, args.author)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    settings = _settings(args)
    results = check(db, authors, members, settings)

    if args.json:
        json.dump({"settings": settings.to_dict(), "authors": [a.to_dict() for a in authors],
                   "summary": summarize(results), "results": [r.to_dict() for r in results]},
                  sys.stdout, indent=2)
        print()
        return 0

    counts = summarize(results)
    print(f"Checked {len(members)} reviewers against {', '.join(a.name for a in authors)}.")
    print(f"Rules: coauthored since {settings.coauthor_cutoff_year}, or shared an institution since "
          f"{settings.institution_cutoff:%Y-%m}. Check these against the venue's policy.\n")
    for level, title in ((CONFLICT, "CONFLICTS (select these)"), (POTENTIAL, "POTENTIAL (review these)")):
        group = [r for r in results if r.level == level]
        if not group:
            continue
        print(f"== {title}: {len(group)}")
        for r in group:
            inst = f" ({r.member.institution})" if r.member.institution else ""
            print(f"* {r.member.name}{inst}")
            for f in r.findings:
                print(f"    [{f.level}] {f.message}")
                for p in f.papers[:args.papers]:
                    print(f"        {p['year']}  {p['title']}  ({p['venue']})")
        print()
    print(f"{counts['clear']} reviewers clear.")
    if args.names_only:
        print("\nConflict names:")
        for r in results:
            if r.level == CONFLICT:
                print(r.member.name)
    return 0


# ---------------------------------------------------------------- serve
def cmd_serve(args) -> int:
    from .server import serve
    serve(home(), port=args.port, open_browser=not args.no_browser)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="clearline",
        description="Find conflicts of interest between a paper's authors and a list of reviewers.")
    ap.add_argument("--version", action="version", version=f"clearline {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("setup", help="download the DBLP dump and import it (run once)")
    p.add_argument("--dump", help="use an already downloaded dblp.xml.gz")
    p.add_argument("--delete-dump", action="store_true", help="delete the 1.1 GB dump after importing")
    p.set_defaults(func=cmd_setup, reuse=False)

    p = sub.add_parser("refresh", help="download the latest DBLP dump and re-import")
    p.add_argument("--delete-dump", action="store_true")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("search", help="look a person up in DBLP")
    p.add_argument("name", nargs="+")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("profile", help="manage COI profiles")
    psub = p.add_subparsers(dest="pcmd", required=True)
    psub.add_parser("new", help="create a profile interactively").set_defaults(func=cmd_profile_new)
    psub.add_parser("list", help="list profiles").set_defaults(func=cmd_profile_list)
    pp = psub.add_parser("show", help="print a profile")
    pp.add_argument("name")
    pp.set_defaults(func=cmd_profile_show)

    p = sub.add_parser("check", help="check a pasted reviewer list for conflicts")
    p.add_argument("pclist", help="file with one reviewer per line, or - for stdin")
    p.add_argument("--profile", "-p", action="append", default=[],
                   help="author profile name or .toml file (repeat for each author who has one)")
    p.add_argument("--author", "-a", action="append", default=[],
                   help="coauthor without a profile: DBLP pid, URL, or exact DBLP name")
    p.add_argument("--coauthor-years", type=int, default=2)
    p.add_argument("--institution-years", type=int, default=2)
    p.add_argument("--max-authors", type=int, default=30,
                   help="papers with more authors only count as potential conflicts")
    p.add_argument("--as-of", help="reference month YYYY-MM (default: today)")
    p.add_argument("--papers", type=int, default=2, help="shared papers to show per finding")
    p.add_argument("--names-only", action="store_true", help="also print conflict names, one per line")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("serve", help="open the local web page")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_serve)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (MissingDatabase, FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
