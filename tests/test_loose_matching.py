import datetime as dt

import pytest

from clearline.names import initial_key, loose_key
from clearline.pclist import PCMember
from clearline.profile import Profile, Relation
from clearline.rules import CONFLICT, POTENTIAL, Settings, build_authors, check

TODAY = Settings(as_of=dt.date(2026, 11, 1))


def run(db, names, profiles=(), ids=("1/alice",)):
    authors, _ = build_authors(db, list(profiles), list(ids))
    members = [PCMember("", n) for n in names]
    return {r.member.name: r for r in check(db, authors, members, TODAY)}


def test_keys():
    assert loose_key("Bob Q. Mueller") == loose_key("Müller Robert") == "muller robert"
    assert loose_key("Wei Wang 0001") == loose_key("Wang Wei")
    assert initial_key("Jane Q. Doe") == "j doe"


def test_exact_match_still_wins(db):
    r = run(db, ["Bob Brown"])["Bob Brown"]
    assert (r.match_kind, r.level) == ("exact", CONFLICT)


@pytest.mark.parametrize("pasted", ["Robert Brown", "Brown Bob", "B. Brown", "Bobby Brown"])
def test_loose_match_is_only_potential(db, pasted):
    # Bob Brown coauthored with Alice in 2025: a conflict on an exact match.
    r = run(db, [pasted])[pasted]
    assert r.match_kind == "loose"
    assert r.match.name == "Bob Brown"
    assert r.level == POTENTIAL
    assert r.findings[0].message.startswith("Loose name match")


def test_umlaut_spelling(db):
    r = run(db, ["Juergen Mueller"])["Juergen Mueller"]
    assert r.match_kind == "loose"
    assert r.match.name == "Jürgen Müller"


def test_loose_ambiguous_name(db):
    r = run(db, ["Wang Wei"])["Wang Wei"]
    assert r.match is None and r.n_namesakes == 3
    assert [c.name for c in r.candidates] == ["Wei Wang 0002"]
    assert r.level == POTENTIAL
    assert "loose name match" in r.findings[0].message


def test_loose_self_is_potential(db):
    r = run(db, ["Ames Alice"])["Ames Alice"]
    assert r.findings[0].kind == "self"
    assert r.level == POTENTIAL


def test_loose_relation(db):
    me = Profile(name="Alice Ames", dblp="1/alice",
                 relations=[Relation("Robert Advisor", "advisor")])
    r = run(db, ["Bob Advisor", "Robert Advisor"], profiles=[me], ids=())
    assert r["Robert Advisor"].level == CONFLICT
    assert r["Bob Advisor"].level == POTENTIAL


def test_nobody_found(db):
    r = run(db, ["Totally Unknown"])["Totally Unknown"]
    assert (r.match, r.match_kind, r.level) == (None, "", "clear")
