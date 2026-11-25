"""Personal COI profiles: affiliation history, email domains, and known relations.

Stored as TOML in ~/.clearline/profiles/<slug>.toml, for example:

    name = "Jane Doe"
    dblp = "123/4567"            # DBLP pid, profile URL, or exact DBLP name

    [[affiliations]]
    institution = "University of Wisconsin-Madison"
    domains = ["wisc.edu"]
    start = "2021-09"
    end = ""                     # empty = current

    [[relations]]
    name = "John Advisor"
    kind = "advisor"             # advisor, student, family, collaborator, other
    note = "PhD advisor"
"""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_MONTH = re.compile(r"^(\d{4})(?:-(\d{1,2}))?$")


def parse_month(text: str | None, *, end: bool = False) -> dt.date | None:
    """'2021-09' -> 2021-09-01. A bare year means January (or December for an end)."""
    if not text:
        return None
    m = _MONTH.match(str(text).strip())
    if not m:
        raise ValueError(f"Expected a month like 2021-09, got {text!r}")
    year = int(m[1])
    month = int(m[2]) if m[2] else (12 if end else 1)
    if not 1 <= month <= 12:
        raise ValueError(f"Bad month in {text!r}")
    return dt.date(year, month, 1)


@dataclass
class Affiliation:
    institution: str
    domains: list[str] = field(default_factory=list)
    start: str = ""
    end: str = ""  # "" means current

    @property
    def start_date(self) -> dt.date | None:
        return parse_month(self.start)

    @property
    def end_date(self) -> dt.date | None:
        return parse_month(self.end, end=True)

    def active_since(self, cutoff: dt.date) -> bool:
        """True if this affiliation lasted until on or after cutoff."""
        end = self.end_date
        return end is None or end >= cutoff

    def span(self) -> str:
        return f"{self.start or '?'} to {self.end or 'present'}"


@dataclass
class Relation:
    name: str
    kind: str = "other"
    note: str = ""


@dataclass
class Profile:
    name: str
    dblp: str = ""
    affiliations: list[Affiliation] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    slug: str = ""

    @classmethod
    def from_dict(cls, d: dict, slug: str = "") -> Profile:
        p = cls(
            name=str(d.get("name", "")).strip(),
            dblp=str(d.get("dblp", "")).strip(),
            affiliations=[Affiliation(
                institution=str(a.get("institution", "")).strip(),
                domains=[str(x).strip().lower().lstrip("@") for x in a.get("domains", []) if str(x).strip()],
                start=str(a.get("start", "")).strip(),
                end=str(a.get("end", "")).strip(),
            ) for a in d.get("affiliations", []) if str(a.get("institution", "")).strip()],
            relations=[Relation(
                name=str(r.get("name", "")).strip(),
                kind=str(r.get("kind", "other")).strip() or "other",
                note=str(r.get("note", "")).strip(),
            ) for r in d.get("relations", []) if str(r.get("name", "")).strip()],
            slug=slug,
        )
        p.validate()
        return p

    def validate(self) -> None:
        if not self.name:
            raise ValueError("A profile needs a name.")
        for a in self.affiliations:
            a.start_date, a.end_date  # noqa: B018 - raises on bad months
            if a.start_date and a.end_date and a.end_date < a.start_date:
                raise ValueError(f"{a.institution}: end {a.end} is before start {a.start}")

    def to_dict(self) -> dict:
        return {
            "name": self.name, "dblp": self.dblp, "slug": self.slug,
            "affiliations": [vars(a) for a in self.affiliations],
            "relations": [vars(r) for r in self.relations],
        }

    def to_toml(self) -> str:
        lines = [f"name = {_q(self.name)}", f"dblp = {_q(self.dblp)}"]
        for a in self.affiliations:
            lines += ["", "[[affiliations]]",
                      f"institution = {_q(a.institution)}",
                      "domains = [" + ", ".join(_q(d) for d in a.domains) + "]",
                      f"start = {_q(a.start)}",
                      f"end = {_q(a.end)}" + ("  # empty = current" if not a.end else "")]
        for r in self.relations:
            lines += ["", "[[relations]]", f"name = {_q(r.name)}",
                      f"kind = {_q(r.kind)}", f"note = {_q(r.note)}"]
        return "\n".join(lines) + "\n"


def _q(s: str) -> str:
    """A TOML basic string."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def slugify(name: str) -> str:
    from .names import fold
    return fold(name).replace(" ", "-") or "profile"


class ProfileStore:
    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def list(self) -> list[Profile]:
        if not self.dir.exists():
            return []
        out = []
        for f in sorted(self.dir.glob("*.toml")):
            try:
                out.append(self.load(f.stem))
            except (ValueError, tomllib.TOMLDecodeError):
                continue  # a broken file should not hide the others
        return out

    def path(self, slug: str) -> Path:
        return self.dir / f"{slugify(slug)}.toml"

    def load(self, slug_or_path: str) -> Profile:
        f = Path(slug_or_path)
        if not (f.suffix == ".toml" and f.exists()):
            f = self.path(slug_or_path)
        if not f.exists():
            raise FileNotFoundError(f"No profile {slug_or_path!r} (looked for {f}).")
        with open(f, "rb") as fh:
            return Profile.from_dict(tomllib.load(fh), slug=f.stem)

    def save(self, profile: Profile) -> Path:
        profile.validate()
        profile.slug = profile.slug or slugify(profile.name)
        self.dir.mkdir(parents=True, exist_ok=True)
        f = self.path(profile.slug)
        f.write_text(profile.to_toml(), encoding="utf-8")
        return f

    def delete(self, slug: str) -> None:
        self.path(slug).unlink(missing_ok=True)
