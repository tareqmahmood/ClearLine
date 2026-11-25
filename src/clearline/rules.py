"""Decide which reviewers conflict with a paper's authors, and why.

Every finding carries evidence (shared papers, matching institutions or email
domains) so a person can check it before ticking a box.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .db import DBLP, Person
from .names import (
    domain_matches,
    email_domain,
    initial_key,
    inst_match,
    inst_score,
    loose_key,
    norm_name,
    short_key,
    starts_with_initial,
)
from .pclist import PCMember
from .profile import Profile

CONFLICT, POTENTIAL, CLEAR = "conflict", "potential", "clear"
_RANK = {CONFLICT: 2, POTENTIAL: 1, CLEAR: 0}


@dataclass
class Settings:
    """Venue COI policy. Check these against the venue's call for papers."""
    coauthor_years: int = 2  # coauthored within this many years -> conflict
    institution_years: int = 2  # shared an institution within this many years -> conflict
    max_authors: int = 30  # papers with more authors only count as potential
    as_of: dt.date = field(default_factory=dt.date.today)

    @property
    def coauthor_cutoff_year(self) -> int:
        # DBLP only has years, so "within 2 years" in Nov 2026 counts all of 2024.
        return self.as_of.year - self.coauthor_years

    @property
    def institution_cutoff(self) -> dt.date:
        y = self.as_of.year - self.institution_years
        return self.as_of.replace(year=y, day=1)

    def to_dict(self) -> dict:
        return {"coauthor_years": self.coauthor_years,
                "institution_years": self.institution_years,
                "max_authors": self.max_authors, "as_of": self.as_of.isoformat(),
                "coauthor_cutoff_year": self.coauthor_cutoff_year,
                "institution_cutoff": self.institution_cutoff.isoformat()[:7]}


@dataclass
class Author:
    """One author of the submission: a profile, a DBLP person, or both."""
    name: str
    person: Person | None = None
    profile: Profile | None = None

    def to_dict(self) -> dict:
        return {"name": self.name, "dblp": self.person.to_dict() if self.person else None,
                "profile": self.profile.slug if self.profile else None}


@dataclass
class Finding:
    level: str
    kind: str  # coauthor, namesake, institution, email-domain, relation, self
    author: str
    message: str
    papers: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return vars(self)


@dataclass
class Result:
    member: PCMember
    match: Person | None = None  # the DBLP person chosen for this reviewer, if any
    candidates: list[Person] = field(default_factory=list)  # namesakes who are coauthors
    match_kind: str = ""  # "exact", "middle-name", "loose", or "" when DBLP has nobody
    n_namesakes: int = 0  # how many DBLP people the name could refer to
    findings: list[Finding] = field(default_factory=list)

    @property
    def level(self) -> str:
        return max((f.level for f in self.findings), key=_RANK.get, default=CLEAR)

    def to_dict(self) -> dict:
        return {
            "member": self.member.to_dict(), "level": self.level,
            "match": self.match.to_dict() if self.match else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "match_kind": self.match_kind, "n_namesakes": self.n_namesakes,
            "findings": [f.to_dict() for f in sorted(
                self.findings, key=lambda f: -_RANK[f.level])],
        }


def build_authors(db: DBLP, profiles: list[Profile], dblp_ids: list[str]) -> tuple[list[Author], list[str]]:
    """Turn profiles and DBLP identifiers into authors. Returns (authors, warnings)."""
    authors, warnings = [], []
    for p in profiles:
        person = db.resolve(p.dblp) if p.dblp else None
        if p.dblp and person is None:
            warnings.append(f"Profile {p.name!r}: DBLP id {p.dblp!r} not found or ambiguous; "
                            "coauthorship checks are skipped for this author. Use the DBLP pid.")
        elif not p.dblp:
            warnings.append(f"Profile {p.name!r} has no DBLP id; coauthorship checks are skipped.")
        authors.append(Author(p.name, person, p))
    for ident in dblp_ids:
        person = db.resolve(ident)
        if person is None:
            n = len(db.ids_exact(ident) or db.ids_short(ident))
            hint = f"{n} DBLP people match that name; use a pid or a name with its 0001 suffix" \
                if n > 1 else "no DBLP match"
            warnings.append(f"Author {ident!r}: {hint}.")
            continue
        authors.append(Author(person.name, person))
    return authors, warnings


@dataclass
class _Match:
    person: Person | None  # chosen DBLP person
    how: str  # "exact", "middle-name", "loose", or ""
    namesakes: list[int]  # every other DBLP person the name could be
    by_institution: bool  # chosen among namesakes by the reviewer's institution


def _match_member(db: DBLP, m: PCMember) -> _Match:
    """Find the DBLP person for a reviewer, and every namesake that could also be them.

    Exact (normalized) names come first. People who differ only by a middle name are
    kept as namesakes, so "Michael Franklin" never hides "Michael J. Franklin". Loose
    matches (nicknames, spellings, order, initials) are used only when nothing else is.
    """
    exact = db.ids_exact(m.name)
    short = [i for i in db.ids_short(m.name) if i not in set(exact)]
    if exact:
        pool, how, extra = exact, "exact", short
    elif short:
        pool, how, extra = short, "middle-name", []
    else:
        pool, how, extra = db.ids_loose(m.name), "loose", []
    if not pool:
        return _Match(None, "", [], False)
    chosen, by_institution = _pick(db, pool, m)
    namesakes = [i for i in pool + extra if i != chosen]
    return _Match(db.person(chosen) if chosen else None, how, namesakes, by_institution)


def _pick(db: DBLP, pool: list[int], m: PCMember) -> tuple[int | None, bool]:
    """Choose among people sharing a name, using the reviewer's institution."""
    if len(pool) == 1:
        return pool[0], False
    if not m.institution:
        return None, False
    affs = db.affiliations_of(pool)

    def score(i: int) -> float:
        cur, old = affs.get(i, ([], []))
        return max([inst_score(m.institution, a) for a in cur]
                   + [0.8 * inst_score(m.institution, a) for a in old] + [0.0])

    scored = sorted(((score(i), i) for i in pool), reverse=True)
    (best, best_id), (runner_up, _) = scored[0], scored[1]
    if best >= 0.7 and best - runner_up >= 0.2:
        return best_id, True
    return None, False


def _paper_list(papers, limit=5) -> list[dict]:
    return [p.to_dict() for p in papers[:limit]]


def _first_aff(p: Person) -> str:
    return f" ({p.affiliations[0]})" if p.affiliations else ""


def check(db: DBLP, authors: list[Author], members: list[PCMember],
          settings: Settings | None = None) -> list[Result]:
    s = settings or Settings()
    cutoff_year = s.coauthor_cutoff_year
    coauthors = {id(a): db.coauthors(a.person.id) if a.person else {} for a in authors}
    author_ids = {a.person.id for a in authors if a.person}
    results = []

    for m in members:
        mm = _match_member(db, m)
        match, how = mm.person, mm.how
        r = Result(m, match, [], how, len(mm.namesakes) + (1 if match else 0))
        domain = email_domain(m.email)
        if how == "loose" and match:
            prefix = f"Loose name match: {m.name!r} looks like DBLP's {match.name!r}. "
        elif how == "middle-name" and match:
            prefix = f"Matched ignoring middle names (DBLP: {match.name!r}). "
        else:
            prefix = ""

        for a in authors:
            n_before = len(r.findings)
            co = coauthors[id(a)]

            # The reviewer is one of the authors.
            if match and a.person and match.id == a.person.id:
                level = POTENTIAL if how == "loose" else CONFLICT
                r.findings.append(Finding(level, "self", a.name,
                                          f"{prefix}This is an author of the paper."))
                continue

            # Coauthorship with the chosen DBLP person.
            if match and match.id in co:
                papers = co[match.id]
                recent = [p for p in papers if (p.year or 0) >= cutoff_year]
                small = [p for p in recent if p.n_authors <= s.max_authors]
                if small:
                    r.findings.append(Finding(
                        CONFLICT, "coauthor", a.name,
                        f"{prefix}Coauthored {len(small)} paper(s) with {a.name} since {cutoff_year} "
                        f"(latest {small[0].year}).", _paper_list(small)))
                elif recent:
                    r.findings.append(Finding(
                        POTENTIAL, "coauthor", a.name,
                        f"{prefix}Coauthored with {a.name} since {cutoff_year}, but only on papers "
                        f"with more than {s.max_authors} authors.", _paper_list(recent)))
                else:
                    r.findings.append(Finding(
                        POTENTIAL, "coauthor", a.name,
                        f"{prefix}Coauthored {len(papers)} paper(s) with {a.name}, most recently in "
                        f"{papers[0].year} (before {cutoff_year}).", _paper_list(papers, 3)))

            # A loosely matched name is never certain enough for a conflict.
            if how == "loose":
                for f in r.findings[n_before:]:
                    f.level = POTENTIAL

            # Every other DBLP person with this name who coauthored with the author.
            for i in mm.namesakes:
                if i not in co or i in author_ids:
                    continue
                c, papers = db.person(i), co[i]
                if c not in r.candidates:
                    r.candidates.append(c)
                shared = (f"coauthored {len(papers)} paper(s) with {a.name} "
                          f"(latest {papers[0].year})")
                if match:
                    why = " by institution" if mm.by_institution else ""
                    msg = (f"{prefix}Matched to DBLP's {match.name!r}{why}, but another DBLP person "
                           f"with this name, {c.name}{_first_aff(c)}, {shared}. "
                           f"Check that the reviewer is not {c.name}.")
                else:
                    loose = " (loose name match)" if how == "loose" else ""
                    msg = (f"DBLP has {r.n_namesakes} people named like {m.name!r}{loose}. "
                           f"One of them, {c.name}{_first_aff(c)}, {shared}. "
                           "Check whether that is this reviewer.")
                r.findings.append(Finding(POTENTIAL, "namesake", a.name, msg, _paper_list(papers, 3)))

            # Relations the author declared in their profile.
            if a.profile:
                for rel in a.profile.relations:
                    note = f": {rel.note}" if rel.note else ""
                    if norm_name(rel.name) == norm_name(m.name) or \
                            short_key(rel.name) == short_key(m.name):
                        r.findings.append(Finding(
                            CONFLICT, "relation", a.name,
                            f"Listed in {a.name}'s profile as {rel.kind}{note}."))
                    elif loose_key(rel.name) == loose_key(m.name) or (
                            starts_with_initial(m.name) and initial_key(rel.name) == initial_key(m.name)):
                        r.findings.append(Finding(
                            POTENTIAL, "relation", a.name,
                            f"Loose name match: {m.name!r} looks like {rel.name!r}, listed in "
                            f"{a.name}'s profile as {rel.kind}{note}."))

            # Institution history from the author's profile.
            if a.profile:
                for aff in a.profile.affiliations:
                    _institution_findings(r, a, aff, m, match, domain, s)

            # No profile: fall back to the author's DBLP affiliation.
            elif a.person and a.person.affiliations:
                theirs = m.institution or (match.affiliations[0] if match and match.affiliations else "")
                for aff in a.person.affiliations:
                    if theirs and inst_match(theirs, aff):
                        r.findings.append(Finding(
                            POTENTIAL, "institution", a.name,
                            f"DBLP lists {a.name} at {aff!r} and the reviewer at {theirs!r}. "
                            f"Add a profile for {a.name} to check dates."))
                        break

        results.append(r)

    results.sort(key=lambda r: (-_RANK[r.level], r.member.name.casefold()))
    return results


def _institution_findings(r: Result, a: Author, aff, m: PCMember, match: Person | None,
                          domain: str, s: Settings) -> None:
    recent = aff.active_since(s.institution_cutoff)
    level = CONFLICT if recent else POTENTIAL
    when = f"{a.name} was there {aff.span()}"
    strength = inst_match(m.institution, aff.institution) if m.institution else None
    if strength == "same":
        r.findings.append(Finding(
            level, "institution", a.name,
            f"Reviewer lists {m.institution!r}; {when} ({aff.institution})."))
    elif domain and any(domain_matches(domain, d) for d in aff.domains):
        r.findings.append(Finding(
            level, "email-domain", a.name,
            f"Reviewer email domain {domain} matches {aff.institution}; {when}."))
    elif strength == "related":
        r.findings.append(Finding(
            POTENTIAL, "institution", a.name,
            f"Reviewer lists {m.institution!r}, related to {aff.institution!r} (another site, "
            f"lab or campus); {when}."))
    elif match and recent and not m.institution and any(
            inst_match(x, aff.institution) for x in match.affiliations):
        r.findings.append(Finding(
            POTENTIAL, "institution", a.name,
            f"DBLP lists the reviewer at {match.affiliations[0]!r}; {when}. "
            "DBLP affiliations can be out of date."))
    if match and recent:
        former = [x for x in match.former_affiliations if inst_match(x, aff.institution)]
        if former:
            r.findings.append(Finding(
                POTENTIAL, "institution", a.name,
                f"DBLP lists the reviewer as formerly at {former[0]!r} (dates not given); {when}."))


def summarize(results: list[Result]) -> dict:
    return {lvl: sum(1 for r in results if r.level == lvl) for lvl in (CONFLICT, POTENTIAL, CLEAR)}
