import datetime as dt

from clearline.pclist import PCMember
from clearline.profile import Affiliation, Profile, Relation
from clearline.rules import CONFLICT, POTENTIAL, Settings, build_authors, check

TODAY = Settings(as_of=dt.date(2026, 11, 1))


def by_name(results):
    return {r.member.name: r for r in results}


def test_import_counts_and_entities(db):
    meta = db.meta()
    assert meta["publications"] == "7"  # proceedings volumes are not coauthorship
    assert db.find("Jurgen Muller")[0].name == "Jürgen Müller"


def test_aliases_and_homonyms(db):
    alice = db.resolve("1/alice")
    assert set(alice.aliases) == {"Alice Ames", "A. Alice"}
    assert alice.affiliations == ["University of Wisconsin-Madison, USA"]
    assert alice.former_affiliations == ["Stanford University, USA"]
    # The bare "Wei Wang" holds papers DBLP has not assigned to anyone yet.
    assert {p.name for p in db.find("Wei Wang")} == {"Wei Wang", "Wei Wang 0001", "Wei Wang 0002"}
    assert db.resolve("https://dblp.org/pid/3/w2.html").name == "Wei Wang 0002"


def test_coauthors_merge_aliases(db):
    alice = db.resolve("1/alice")
    co = {db.person(pid).name: papers for pid, papers in db.coauthors(alice.id).items()}
    assert set(co) == {"Bob Brown", "Jürgen Müller", "Wei Wang 0002", "Dan Dorsey",
                       "Michael J. Franklin", "Lei Zhang 0030"}
    assert co["Jürgen Müller"][0].year == 2015
    assert "Carol Chen" not in co  # only co-editors


def run(db, members, profiles=(), ids=(), settings=TODAY):
    authors, warnings = build_authors(db, list(profiles), list(ids))
    return by_name(check(db, authors, members, settings)), warnings


def test_coauthor_levels(db):
    r, _ = run(db, [PCMember("", "Bob Brown"), PCMember("", "Jurgen Muller"),
                    PCMember("", "Dan Dorsey"), PCMember("", "Nobody Here")], ids=["1/alice"])
    assert r["Bob Brown"].level == CONFLICT
    assert r["Jurgen Muller"].level == POTENTIAL  # 2015 is outside the window
    assert r["Nobody Here"].level == "clear"

    small = Settings(as_of=dt.date(2026, 11, 1), max_authors=1)
    r, _ = run(db, [PCMember("", "Dan Dorsey")], ids=["1/alice"], settings=small)
    assert r["Dan Dorsey"].level == POTENTIAL  # only a paper over the author limit


def test_ambiguous_name_resolved_by_institution(db):
    r, _ = run(db, [PCMember("", "Wei Wang", "UNSW Sydney, University of New South Wales")],
               ids=["1/alice"])
    assert r["Wei Wang"].match.name == "Wei Wang 0002"
    assert r["Wei Wang"].level == CONFLICT

    # Matched to the UCLA Wei Wang, but the UNSW Wei Wang (a coauthor) is still reported.
    r, _ = run(db, [PCMember("", "Wei Wang", "UCLA")], ids=["1/alice"])
    assert r["Wei Wang"].match.name == "Wei Wang 0001"
    assert r["Wei Wang"].level == POTENTIAL
    assert [f.kind for f in r["Wei Wang"].findings] == ["namesake"]


def test_ambiguous_name_without_institution_is_potential(db):
    r, _ = run(db, [PCMember("", "Wei Wang")], ids=["1/alice"])
    assert r["Wei Wang"].match is None
    assert r["Wei Wang"].n_namesakes == 3
    assert [c.name for c in r["Wei Wang"].candidates] == ["Wei Wang 0002"]
    assert r["Wei Wang"].level == POTENTIAL
    assert r["Wei Wang"].findings[0].kind == "namesake"


def test_profile_institution_domain_and_relations(db):
    me = Profile(
        name="Alice Ames", dblp="1/alice",
        affiliations=[
            Affiliation("University of Wisconsin-Madison", ["wisc.edu"], "2023-09", ""),
            Affiliation("Stanford University", ["stanford.edu"], "2016-09", "2025-06"),
            Affiliation("Old Corp Research", ["oldcorp.com"], "2010-01", "2015-12"),
        ],
        relations=[Relation("Pat Advisor", "advisor", "PhD advisor")],
    )
    r, warnings = run(db, [
        PCMember("", "Zed Zee", "University of Wisconsin - Madison"),
        PCMember("", "Yan Yu", email="yan@cs.stanford.edu"),
        PCMember("", "Xi Xu", "Old Corp Research Labs"),
        PCMember("", "Pat Advisor", "Elsewhere"),
        PCMember("", "Gmail User", email="someone@gmail.com"),
    ], profiles=[me])
    assert not warnings
    assert r["Zed Zee"].level == CONFLICT  # current institution
    assert r["Yan Yu"].level == CONFLICT  # stanford.edu, left 2025-06, inside 2 years
    assert r["Xi Xu"].level == POTENTIAL  # left in 2015
    assert r["Pat Advisor"].level == CONFLICT
    assert r["Gmail User"].level == "clear"  # public email domains never match


def test_reviewer_who_is_an_author(db):
    r, _ = run(db, [PCMember("", "Alice Ames")], ids=["1/alice"])
    assert r["Alice Ames"].findings[0].kind == "self"


def test_unknown_author_warns(db):
    _, warnings = run(db, [PCMember("", "Bob Brown")], ids=["Wei Wang"])
    assert "3 DBLP people" in warnings[0]
