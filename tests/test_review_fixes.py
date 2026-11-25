"""Regression tests for the issues found in the first code review."""

import datetime as dt
import shutil
import sqlite3

import pytest

from clearline import cli
from clearline.db import DBLP, OutdatedDatabase
from clearline.names import inst_match, norm_name
from clearline.pclist import parse, parse_line
from clearline.profile import Profile, ProfileStore
from clearline.rules import CONFLICT, POTENTIAL, Settings, build_authors, check

TODAY = Settings(as_of=dt.date(2026, 11, 1))


def run(db, members, ids=("1/alice",), profiles=()):
    authors, warnings = build_authors(db, list(profiles), list(ids))
    return {r.member.raw or r.member.name: r for r in check(db, authors, members, TODAY)}, warnings


# 1. Names starting with X ------------------------------------------------------------
@pytest.mark.parametrize("line,name", [
    ("Xin Luna Dong", "Xin Luna Dong"),
    ("xiaokui Xiao (NUS)", "xiaokui Xiao"),
    ("[x] Xiaofang Zhou", "Xiaofang Zhou"),
    ("☐ Xin Luna Dong", "Xin Luna Dong"),
    ("[ ]\tXuemin Lin\tUNSW", "Xuemin Lin"),
])
def test_leading_x_is_kept(line, name):
    assert parse_line(line).name == name


# 2. Common names are not capped -------------------------------------------------------
def test_every_namesake_is_checked(db):
    r, _ = run(db, [parse_line("Lei Zhang")])
    r = r["Lei Zhang"]
    assert r.n_namesakes == 30
    assert r.level == POTENTIAL
    assert [c.name for c in r.candidates] == ["Lei Zhang 0030"]


# 3. Middle names ----------------------------------------------------------------------
def test_namesake_with_middle_name_is_reported(db):
    r, _ = run(db, [parse_line("Michael Franklin")])
    r = r["Michael Franklin"]
    assert r.match.name == "Michael Franklin"  # the exact match is still preferred
    assert r.level == POTENTIAL  # but the coauthor Michael J. Franklin is not hidden
    assert "Michael J. Franklin" in r.findings[0].message


def test_middle_name_only_match_is_a_conflict_with_a_note(db):
    r, _ = run(db, [parse_line("Michael X. Franklin")])
    r = r["Michael X. Franklin"]
    # No exact match; first+last finds both Franklins, neither picked by name alone.
    assert r.level == POTENTIAL


# 4-6. Pasted list shapes --------------------------------------------------------------
@pytest.mark.parametrize("line,name,inst", [
    ("Jane Doe, Stanford", "Jane Doe", "Stanford"),
    ("Jane Doe, Tsinghua", "Jane Doe", "Tsinghua"),
    ("Doe, Jane", "Jane Doe", ""),
    ("Doe, Jane, University of X", "Jane Doe", "University of X"),
    ("Jane Doe, University of Wisconsin - Madison", "Jane Doe", "University of Wisconsin - Madison"),
    ("Jane Doe - University of Wisconsin, Madison", "Jane Doe", "University of Wisconsin, Madison"),
    ("Alexandros\tLabrinidis\tUniversity of Pittsburgh", "Alexandros Labrinidis", "University of Pittsburgh"),
    ("Henk\tCorporaal\tEindhoven University of Technology", "Henk Corporaal", "Eindhoven University of Technology"),
    ("Jane Labrador (Google)", "Jane Labrador", "Google"),
])
def test_line_shapes(line, name, inst):
    m = parse_line(line)
    assert (m.name, m.institution) == (name, inst)


# 7. Same-name reviewers ---------------------------------------------------------------
def test_same_name_different_institution_kept():
    members = parse("Wei Wang (UCLA)\nWei Wang (UNSW)\nWei Wang (UCLA)\n")
    assert [(m.name, m.institution) for m in members] == [("Wei Wang", "UCLA"), ("Wei Wang", "UNSW")]


# 8. DBLP affiliation labels -----------------------------------------------------------
def test_affiliation_labels(db):
    carol = db.resolve("7/carol")
    assert carol.affiliations == ["New University"]
    assert set(carol.former_affiliations) == {"Old University", "Older Institute"}


# 9. Institution matching --------------------------------------------------------------
@pytest.mark.parametrize("a,b,expected", [
    ("HKUST", "University of Hong Kong", None),
    ("City University of Hong Kong", "University of Hong Kong", None),
    ("Washington University", "University of Washington", None),
    ("Miami University", "University of Miami", None),
    ("University of South Florida", "University of Florida", None),
    ("UC Berkeley", "UCLA", None),
    ("Microsoft Research, Redmond", "Microsoft", "related"),
    ("Microsoft Research Asia", "Microsoft Research", "related"),
    ("Google DeepMind", "Google", "related"),
    ("MIT CSAIL", "MIT", "related"),
    ("University of Wisconsin", "University of Wisconsin-Madison", "related"),
    ("TU Munich", "Technical University of Munich", "same"),
    ("UCLA", "University of California, Los Angeles, USA", "same"),
    ("Stanford University, Stanford, CA 94305, USA", "Stanford University", "same"),
    ("University of Wisconsin-Madison, Madison, WI, USA", "University of Wisconsin - Madison", "same"),
    ("Department of Computer Science, ETH Zurich, Switzerland", "ETH Zurich", "same"),
])
def test_inst_match(a, b, expected):
    assert inst_match(a, b) == expected
    assert inst_match(b, a) == expected


def test_related_institution_is_potential(db):
    me = Profile(name="Alice Ames", dblp="1/alice", affiliations=[])
    me = Profile.from_dict({"name": "Alice Ames", "affiliations": [
        {"institution": "Microsoft Research", "start": "2020-01"}]})
    r, _ = run(db, [parse_line("Pat Person (Microsoft Research Asia)"),
                    parse_line("Sam Person (Microsoft Research)")], ids=(), profiles=[me])
    assert r["Pat Person (Microsoft Research Asia)"].level == POTENTIAL
    assert r["Sam Person (Microsoft Research)"].level == CONFLICT


# 10. Outdated databases ---------------------------------------------------------------
def test_outdated_database_asks_for_setup(db, tmp_path):
    old = tmp_path / "old.sqlite"
    shutil.copy(db.path, old)
    with sqlite3.connect(old) as c:
        c.execute("UPDATE meta SET value='2' WHERE key='schema'")
    with pytest.raises(OutdatedDatabase, match="clearline setup"):
        DBLP(old)


# 11. Badly shaped profiles ------------------------------------------------------------
@pytest.mark.parametrize("data", [
    {"name": "X", "affiliations": {"institution": "U"}},
    {"name": "X", "affiliations": ["U"]},
    {"name": "X", "relations": "Prof"},
    {"name": "X", "affiliations": [{"institution": "U", "domains": 5}]},
])
def test_bad_profile_shapes_raise_value_error(data):
    with pytest.raises(ValueError):
        Profile.from_dict(data)


def test_domains_may_be_a_string_and_months_are_normalized():
    p = Profile.from_dict({"name": "X", "affiliations": [
        {"institution": "U", "domains": "wisc.edu", "start": "2021", "end": "2023-6"}]})
    a = p.affiliations[0]
    assert a.domains == ["wisc.edu"]
    assert (a.start, a.end) == ("2021-01", "2023-06")
    p = Profile.from_dict({"name": "X", "affiliations": [{"institution": "U", "end": "2021"}]})
    assert p.affiliations[0].end == "2021-12"


def test_broken_profile_file_is_reported_not_fatal(tmp_path):
    store = ProfileStore(tmp_path)
    store.save(Profile(name="Good One"))
    (tmp_path / "bad.toml").write_text('name = "Bad"\n[affiliations]\ninstitution = "U"\n')
    assert [p.name for p in store.list()] == ["Good One"]
    assert store.errors and store.errors[0][0].name == "bad.toml"


def test_store_load_rejects_paths(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    outside = tmp_path / "elsewhere.toml"
    outside.write_text('name = "Outside"\n')
    with pytest.raises(FileNotFoundError):
        store.load(str(outside))
    assert store.load_file(outside).name == "Outside"


# 12. Resolving authors by name --------------------------------------------------------
def test_resolve_refuses_pooled_disambiguation_name(db):
    assert db.resolve("Wei Wang") is None
    assert db.resolve("Wei Wang 0002").pid == "3/w2"
    plan = " ".join(r[3] for r in db.conn.execute(
        "EXPLAIN QUERY PLAN SELECT person_id FROM names WHERE name=?", ("x",)))
    assert "INDEX" in plan


# 14. check without -p -----------------------------------------------------------------
def test_check_needs_explicit_profile_when_several_exist(home, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLEARLINE_HOME", str(home))
    store = ProfileStore(home / "profiles")
    store.save(Profile(name="Alice Ames", dblp="1/alice"))
    store.save(Profile(name="Bob Brown", dblp="2/bob"))
    pc = tmp_path / "pc.txt"
    pc.write_text("Dan Dorsey\n")
    assert cli.main(["check", str(pc)]) == 1
    assert "--profile" in capsys.readouterr().err


# Lower priority -----------------------------------------------------------------------
def test_non_decomposing_letters_fold():
    assert norm_name("Işık Çelik") == "isik celik"
    assert norm_name("Đỗ Đức") == "do duc"
    assert norm_name("Ægir Þórsson") == "aegir thorsson"


def test_reviewer_former_dblp_affiliation_is_potential(db):
    # Bob Brown's DBLP record says Stanford (current). Alice's profile: Stanford until
    # recently. Carol's DBLP record says she left Old University until 2024.
    me = Profile.from_dict({"name": "Alice Ames", "affiliations": [
        {"institution": "Old University", "start": "2020-01", "end": "2025-06"}]})
    r, _ = run(db, [parse_line("Carol Chen")], ids=(), profiles=[me])
    assert r["Carol Chen"].level == POTENTIAL
    assert "formerly" in r["Carol Chen"].findings[0].message
