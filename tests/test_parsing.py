import pytest

from clearline.names import domain_matches, norm_name, same_institution
from clearline.pclist import parse, parse_line
from clearline.profile import Profile, ProfileStore


@pytest.mark.parametrize("line,name,inst,email", [
    ("Jane Doe", "Jane Doe", "", ""),
    ("Jane Doe (University of Somewhere)", "Jane Doe", "University of Somewhere", ""),
    ("Jane Doe, University of Somewhere", "Jane Doe", "University of Somewhere", ""),
    ("Jane Doe - Google", "Jane Doe", "Google", ""),
    ("Jane Doe <jane@x.edu>", "Jane Doe", "", "jane@x.edu"),
    ("Doe, Jane", "Jane Doe", "", ""),
    ("Jane\tDoe\tjane@x.edu\tUniversity of X", "Jane Doe", "University of X", "jane@x.edu"),
    ("☐\tJane Doe\tMicrosoft Research", "Jane Doe", "Microsoft Research", ""),
    ("Jane Doe, jane@x.edu, ETH Zurich", "Jane Doe", "ETH Zurich", "jane@x.edu"),
])
def test_parse_line(line, name, inst, email):
    m = parse_line(line)
    assert (m.name, m.institution, m.email) == (name, inst, email)


def test_parse_skips_headers_blanks_and_duplicates():
    text = "Name\tOrganization\n\nJane Doe\nJane Doe\nJohn Roe\n"
    assert [m.name for m in parse(text)] == ["Jane Doe", "John Roe"]


def test_names_and_institutions():
    assert norm_name("Jürgen Müller 0003") == "jurgen muller"
    assert norm_name("Jean-Luc O'Brien") == "jean luc obrien"
    assert same_institution("UW-Madison", "University of Wisconsin Madison")
    assert same_institution("Stanford University, USA", "stanford university")
    assert same_institution("University of Wisconsin-Madison, Madison, WI, USA",
                            "University of Wisconsin - Madison")
    assert same_institution("Department of Computer Science, ETH Zurich, Switzerland", "ETH Zurich")
    assert not same_institution("University of Washington", "University of Wisconsin")
    assert not same_institution("University of California, Berkeley", "University of California")
    assert not same_institution("UC Berkeley", "UCLA")
    assert not same_institution("University of South Florida", "University of Florida")
    assert same_institution("Stanford University, Stanford, CA 94305, USA", "Stanford University")
    assert domain_matches("cs.wisc.edu", "wisc.edu")
    assert not domain_matches("notwisc.edu", "wisc.edu")
    assert not domain_matches("gmail.com", "gmail.com")


def test_profile_round_trip(tmp_path):
    store = ProfileStore(tmp_path)
    p = Profile.from_dict({
        "name": 'Jane "JD" Doe', "dblp": "1/2",
        "affiliations": [{"institution": "Uni", "domains": ["@Uni.edu"], "start": "2020-01", "end": ""}],
        "relations": [{"name": "Prof X", "kind": "advisor"}],
    })
    store.save(p)
    loaded = store.load(p.slug)
    assert loaded.name == 'Jane "JD" Doe'
    assert loaded.affiliations[0].domains == ["uni.edu"]
    assert loaded.relations[0].kind == "advisor"


def test_profile_rejects_bad_dates():
    with pytest.raises(ValueError):
        Profile.from_dict({"name": "X", "affiliations": [
            {"institution": "U", "start": "2022-05", "end": "2021-01"}]})
    with pytest.raises(ValueError):
        Profile.from_dict({"name": "X", "affiliations": [{"institution": "U", "start": "May 2022"}]})
