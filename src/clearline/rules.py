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
    inst_similarity,
    loose_key,
    norm_name,
    same_institution,
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
    kind: str  # coauthor, institution, email-domain, relation, self, ambiguous-name
    author: str
    message: str
    papers: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return vars(self)


@dataclass
class Result:
    member: PCMember
    match: Person | None = None
    candidates: list[Person] = field(default_factory=list)  # set when the name is ambiguous
    match_kind: str = ""  # "exact", "loose", or "" when DBLP has nobody by that name
    findings: list[Finding] = field(default_factory=list)

    @property
    def level(self) -> str:
        return max((f.level for f in self.findings), key=_RANK.get, default=CLEAR)

    def to_dict(self) -> dict:
        return {
            "member": self.member.to_dict(), "level": self.level,
            "match": self.match.to_dict() if self.match else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "match_kind": self.match_kind,
            "findings": [f.to_dict() for f in sorted(
                self.findings, key=lambda f: -_RANK[f.level])],
        }


def build_authors(db: DBLP, profiles: list[Profile], dblp_ids: list[str]) -> tuple[list[Author], list[str]]:
    """Turn profiles and DBLP identifiers into authors. Returns (authors, warnings)."""
    authors, warnings = [], []
    for p in profiles:
        person = db.resolve(p.dblp) if p.dblp else None
        if p.dblp and person is None:
            warnings.append(f"Profile {p.name!r}: DBLP id {p.dblp!r} not found; "
                            "coauthorship checks are skipped for this author.")
        elif not p.dblp:
            warnings.append(f"Profile {p.name!r} has no DBLP id; coauthorship checks are skipped.")
        authors.append(Author(p.name, person, p))
    for ident in dblp_ids:
        person = db.resolve(ident)
        if person is None:
            n = len(db.find(ident))
            hint = f"{n} DBLP people match that name; use a pid or a name with its 0001 suffix" \
                if n > 1 else "no DBLP match"
            warnings.append(f"Author {ident!r}: {hint}.")
            continue
        authors.append(Author(person.name, person))
    return authors, warnings


def _match_member(db: DBLP, m: PCMember) -> tuple[Person | None, list[Person], str]:
    """Find the DBLP person for a reviewer.

    Returns (match, ambiguous_candidates, how) where how is "exact", "loose", or "".
    """
    how = "exact"
    cands = db.find(m.name)
    if not cands:
        cands, how = db.find_loose(m.name), "loose"
    if not cands:
        return None, [], ""
    match, ambiguous = _pick(cands, m)
    return match, ambiguous, how


def _pick(cands: list[Person], m: PCMember) -> tuple[Person | None, list[Person]]:
    """Choose among people sharing a name, using the reviewer's institution."""
    if len(cands) == 1:
        return cands[0], []
    if m.institution:
        def score(p: Person) -> float:
            cur = max((inst_similarity(m.institution, a) for a in p.affiliations), default=0)
            old = max((inst_similarity(m.institution, a) for a in p.former_affiliations), default=0)
            return max(cur, 0.8 * old)
        scored = sorted(((score(p), p) for p in cands), key=lambda t: -t[0])
        best, runner_up = scored[0][0], scored[1][0]
        if best >= 0.5 and best - runner_up >= 0.2:
            return scored[0][1], []
    return None, cands


def _paper_list(papers, limit=5) -> list[dict]:
    return [p.to_dict() for p in papers[:limit]]


def check(db: DBLP, authors: list[Author], members: list[PCMember],
          settings: Settings | None = None) -> list[Result]:
    s = settings or Settings()
    cutoff_year = s.coauthor_cutoff_year
    coauthors = {id(a): db.coauthors(a.person.id) if a.person else {} for a in authors}
    results = []

    for m in members:
        match, cands, how = _match_member(db, m)
        r = Result(m, match, cands, how)
        domain = email_domain(m.email)

        for a in authors:
            n_before = len(r.findings)
            co = coauthors[id(a)]

            # The reviewer is one of the authors.
            if match and a.person and match.id == a.person.id:
                if how == "loose":
                    r.findings.append(Finding(
                        POTENTIAL, "self", a.name,
                        f"Loose name match: {m.name!r} looks like {a.name}, an author of the paper."))
                else:
                    r.findings.append(Finding(CONFLICT, "self", a.name, "This is an author of the paper."))
                continue

            # Coauthorship from DBLP.
            if match and match.id in co:
                papers = co[match.id]
                recent = [p for p in papers if (p.year or 0) >= cutoff_year]
                small = [p for p in recent if p.n_authors <= s.max_authors]
                if small:
                    r.findings.append(Finding(
                        CONFLICT, "coauthor", a.name,
                        f"Coauthored {len(small)} paper(s) with {a.name} since {cutoff_year} "
                        f"(latest {small[0].year}).", _paper_list(small)))
                elif recent:
                    r.findings.append(Finding(
                        POTENTIAL, "coauthor", a.name,
                        f"Coauthored with {a.name} since {cutoff_year}, but only on papers with "
                        f"more than {s.max_authors} authors.", _paper_list(recent)))
                else:
                    r.findings.append(Finding(
                        POTENTIAL, "coauthor", a.name,
                        f"Coauthored {len(papers)} paper(s) with {a.name}, most recently in "
                        f"{papers[0].year} (before {cutoff_year}).", _paper_list(papers, 3)))
            elif cands:
                hits = [c for c in cands if c.id in co]
                if how == "loose" and len(cands) > 1:
                    # The ambiguity message below already says to check identity.
                    how_note = f" (loose name match for {m.name!r})"
                else:
                    how_note = ""
                for c in hits:
                    papers = co[c.id]
                    aff = f" ({c.affiliations[0]})" if c.affiliations else ""
                    r.findings.append(Finding(
                        POTENTIAL, "ambiguous-name", a.name,
                        f"DBLP has {len(cands)} people named like {m.name!r}{how_note}. One of "
                        f"them, {c.name}{aff}, coauthored {len(papers)} paper(s) with {a.name} "
                        f"(latest {papers[0].year}). Check whether that is this reviewer.",
                        _paper_list(papers, 3)))

            # A loosely matched name is never certain enough for a conflict.
            if how == "loose" and match:
                for f in r.findings[n_before:]:
                    f.level = POTENTIAL
                    f.message = (f"Loose name match: {m.name!r} looks like DBLP's {match.name!r}. "
                                 + f.message)

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
                    recent = aff.active_since(s.institution_cutoff)
                    level = CONFLICT if recent else POTENTIAL
                    when = f"{a.name} was there {aff.span()}"
                    if m.institution and same_institution(m.institution, aff.institution):
                        r.findings.append(Finding(
                            level, "institution", a.name,
                            f"Reviewer lists {m.institution!r}; {when} ({aff.institution})."))
                    elif domain and any(domain_matches(domain, d) for d in aff.domains):
                        r.findings.append(Finding(
                            level, "email-domain", a.name,
                            f"Reviewer email domain {domain} matches {aff.institution}; {when}."))
                    elif match and recent and not m.institution and any(
                            same_institution(x, aff.institution) for x in match.affiliations):
                        r.findings.append(Finding(
                            POTENTIAL, "institution", a.name,
                            f"DBLP lists the reviewer at {match.affiliations[0]!r}; {when}. "
                            "DBLP affiliations can be out of date."))

            # No profile: fall back to the author's DBLP affiliation.
            elif a.person and a.person.affiliations:
                theirs = m.institution or (match.affiliations[0] if match and match.affiliations else "")
                for aff in a.person.affiliations:
                    if theirs and same_institution(theirs, aff):
                        r.findings.append(Finding(
                            POTENTIAL, "institution", a.name,
                            f"DBLP lists {a.name} at {aff!r} and the reviewer at {theirs!r}. "
                            f"Add a profile for {a.name} to check dates."))
                        break

        results.append(r)

    results.sort(key=lambda r: (-_RANK[r.level], r.member.name.casefold()))
    return results


def summarize(results: list[Result]) -> dict:
    return {lvl: sum(1 for r in results if r.level == lvl) for lvl in (CONFLICT, POTENTIAL, CLEAR)}
