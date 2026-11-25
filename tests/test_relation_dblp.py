import datetime as dt
import tomllib

from clearline.pclist import PCMember
from clearline.profile import Profile, Relation
from clearline.rules import CONFLICT, Settings, build_authors, check

TODAY = Settings(as_of=dt.date(2026, 11, 1))


def run(db, me, names):
    authors, _ = build_authors(db, [me], [])
    members = [PCMember("", n, inst) for n, inst in names]
    return {(r.member.name, r.member.institution): r for r in check(db, authors, members, TODAY)}


def test_relation_dblp_roundtrips_through_toml():
    me = Profile(name="Alice Ames", relations=[Relation("Wei Wang", "advisor", "PhD", dblp="3/w2")])
    back = Profile.from_dict(tomllib.loads(me.to_toml()))
    assert back.relations[0].dblp == "3/w2"
    assert Profile.from_dict(me.to_dict()).relations[0].dblp == "3/w2"


def test_old_relations_without_dblp_still_load():
    p = Profile.from_dict(tomllib.loads('name = "A"\n[[relations]]\nname = "Bob"\nkind = "advisor"\n'))
    assert p.relations[0].dblp == ""


def test_linked_relation_matches_by_identity(db):
    # The advisor is Wei Wang 0002 (UNSW). The UNSW reviewer is a conflict;
    # the UCLA Wei Wang is a different person and must not be.
    me = Profile(name="Alice Ames", relations=[Relation("Wei Wang", "advisor", dblp="3/w2")])
    r = run(db, me, [("Wei Wang", "UNSW"), ("Wei Wang", "UCLA")])
    assert any(f.kind == "relation" and f.level == CONFLICT for f in r["Wei Wang", "UNSW"].findings)
    assert not any(f.kind == "relation" for f in r["Wei Wang", "UCLA"].findings)


def test_linked_relation_catches_other_spelling(db):
    # DBLP merges aliases, so a reviewer listed under another name is still caught.
    me = Profile(name="Alice Ames", relations=[Relation("Jürgen Müller", "advisor", dblp="4/j")])
    r = run(db, me, [("Juergen Mueller", "")])
    assert any(f.kind == "relation" for f in r["Juergen Mueller", ""].findings)


def test_linked_relation_unidentified_reviewer_is_potential(db):
    me = Profile(name="Alice Ames", relations=[Relation("Wei Wang", "advisor", dblp="3/w2")])
    r = run(db, me, [("Wei Wang", "")])
    rel = [f for f in r["Wei Wang", ""].findings if f.kind == "relation"]
    assert [f.level for f in rel] == ["potential"]
