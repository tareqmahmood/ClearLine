"""Read-only queries over the imported DBLP database."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .names import norm_name, short_key


@dataclass
class Person:
    id: int
    pid: str | None  # DBLP person id, e.g. "123/4567"; None if DBLP has no profile page
    name: str  # DBLP's display name, may carry a "0001" suffix
    aliases: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)  # current first
    former_affiliations: list[str] = field(default_factory=list)
    n_pubs: int = 0
    last_year: int | None = None

    @property
    def url(self) -> str | None:
        return f"https://dblp.org/pid/{self.pid}.html" if self.pid else None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "pid": self.pid, "name": self.name, "url": self.url,
            "aliases": self.aliases, "affiliations": self.affiliations,
            "former_affiliations": self.former_affiliations,
            "n_pubs": self.n_pubs, "last_year": self.last_year,
        }


@dataclass
class Paper:
    key: str
    year: int | None
    title: str
    venue: str
    n_authors: int

    @property
    def url(self) -> str:
        return f"https://dblp.org/rec/{self.key}.html"

    def to_dict(self) -> dict:
        return {"key": self.key, "year": self.year, "title": self.title,
                "venue": self.venue, "n_authors": self.n_authors, "url": self.url}


class MissingDatabase(RuntimeError):
    pass


class DBLP:
    def __init__(self, path: Path):
        if not Path(path).exists():
            raise MissingDatabase(
                f"No DBLP database at {path}. Run `clearline setup` to download and import DBLP.")
        self.path = Path(path)
        self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.person = lru_cache(maxsize=4096)(self._person)
        self.coauthors = lru_cache(maxsize=256)(self._coauthors)

    def meta(self) -> dict:
        return {r["key"]: r["value"] for r in self.conn.execute("SELECT key, value FROM meta")}

    def _person(self, person_id: int) -> Person | None:
        row = self.conn.execute("SELECT id, pid, name FROM persons WHERE id=?", (person_id,)).fetchone()
        if row is None:
            return None
        p = Person(row["id"], row["pid"], row["name"])
        p.aliases = [r[0] for r in self.conn.execute(
            "SELECT name FROM names WHERE person_id=? ORDER BY id", (person_id,))]
        for r in self.conn.execute(
                "SELECT text, former FROM affiliations WHERE person_id=? ORDER BY rowid", (person_id,)):
            (p.former_affiliations if r["former"] else p.affiliations).append(r["text"])
        stats = self.conn.execute(
            "SELECT count(*), max(p.year) FROM names n JOIN authorship a ON a.name_id=n.id "
            "JOIN pubs p ON p.id=a.pub_id WHERE n.person_id=?", (person_id,)).fetchone()
        p.n_pubs, p.last_year = stats[0], stats[1]
        return p

    def find(self, name: str, limit: int = 25) -> list[Person]:
        """People whose name (or an alias) matches. Falls back to first+last name."""
        ids = self._ids("norm", norm_name(name), limit)
        if not ids:
            ids = self._ids("short", short_key(name), limit)
        people = [self.person(i) for i in ids]
        return sorted((p for p in people if p), key=lambda p: -p.n_pubs)

    def _ids(self, column: str, key: str, limit: int) -> list[int]:
        if not key:
            return []
        rows = self.conn.execute(
            f"SELECT DISTINCT person_id FROM names WHERE {column}=? LIMIT ?", (key, limit))
        return [r[0] for r in rows]

    def resolve(self, ident: str) -> Person | None:
        """A DBLP pid ('123/4567'), profile URL, or exact DBLP name ('Wei Wang 0001')."""
        ident = ident.strip()
        for prefix in ("https://dblp.org/pid/", "http://dblp.org/pid/",
                       "https://dblp.org/pers/", "dblp.org/pid/"):
            if ident.startswith(prefix):
                ident = ident[len(prefix):].removesuffix(".html").removesuffix(".xml")
        if "/" in ident:
            row = self.conn.execute("SELECT id FROM persons WHERE pid=?", (ident,)).fetchone()
            if row:
                return self.person(row[0])
        row = self.conn.execute("SELECT person_id FROM names WHERE name=?", (ident,)).fetchone()
        if row:
            return self.person(row[0])
        matches = self.find(ident)
        return matches[0] if len(matches) == 1 else None

    def _coauthors(self, person_id: int) -> dict[int, list[Paper]]:
        """Every coauthor of person_id with the papers they share, newest first."""
        rows = self.conn.execute(
            """
            SELECT n2.person_id AS other, p.key, p.year, p.title, p.venue, p.n_authors
            FROM names n1
            JOIN authorship a1 ON a1.name_id = n1.id
            JOIN authorship a2 ON a2.pub_id = a1.pub_id
            JOIN names n2 ON n2.id = a2.name_id
            JOIN pubs p ON p.id = a1.pub_id
            WHERE n1.person_id = ? AND n2.person_id != ?
            """, (person_id, person_id))
        out: dict[int, list[Paper]] = {}
        for r in rows:
            out.setdefault(r["other"], []).append(
                Paper(r["key"], r["year"], r["title"], r["venue"], r["n_authors"]))
        for papers in out.values():
            papers.sort(key=lambda p: -(p.year or 0))
        return out
