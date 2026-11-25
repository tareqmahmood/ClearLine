import gzip
from pathlib import Path

import pytest

from clearline.db import DBLP
from clearline.dblp_import import import_dump

# A tiny DBLP dump. Two different people are named "Wei Wang" (0001 and 0002) and a
# disambiguation page holds unassigned "Wei Wang" papers; "J&uuml;rgen" exercises entity
# decoding; "A. Alice" is an alias of Alice Ames; "Michael Franklin" is not the coauthor
# "Michael J. Franklin"; 30 people share the name "Lei Zhang"; Carol Chen has dated labels.
DUMP = """<?xml version="1.0" encoding="ISO-8859-1"?>
<!DOCTYPE dblp SYSTEM "dblp.dtd">
<dblp>
<www key="homepages/1/alice"><author>Alice Ames</author><author>A. Alice</author><title>Home Page</title>
  <note type="affiliation">University of Wisconsin-Madison, USA</note>
  <note type="affiliation" label="former">Stanford University, USA</note></www>
<www key="homepages/2/bob"><author>Bob Brown</author><title>Home Page</title>
  <note type="affiliation">Stanford University, USA</note></www>
<www key="homepages/3/w1"><author>Wei Wang 0001</author><title>Home Page</title>
  <note type="affiliation">University of California, Los Angeles, USA</note></www>
<www key="homepages/3/w2"><author>Wei Wang 0002</author><title>Home Page</title>
  <note type="affiliation">University of New South Wales, Sydney, Australia</note></www>
<www key="homepages/4/j"><author>J&uuml;rgen M&uuml;ller</author><title>Home Page</title></www>
<inproceedings key="conf/sigmod/AmesB25"><author>Alice Ames</author><author>Bob Brown</author>
  <title>Recent <i>paper</i>.</title><year>2025</year><booktitle>SIGMOD</booktitle></inproceedings>
<article key="journals/pvldb/AmesM15"><author>A. Alice</author><author>J&uuml;rgen M&uuml;ller</author>
  <title>Old paper.</title><year>2015</year><journal>PVLDB</journal></article>
<article key="journals/tods/AmesW24"><author>Alice Ames</author><author>Wei Wang 0002</author>
  <title>Ambiguous paper.</title><year>2024</year><journal>TODS</journal></article>
<proceedings key="conf/x/2025"><editor>Alice Ames</editor><editor>Carol Chen</editor>
  <title>Proceedings.</title><year>2025</year></proceedings>
<article key="journals/big/X25"><author>Alice Ames</author><author>Dan Dorsey</author>
  <title>Huge collaboration.</title><year>2025</year><journal>Big</journal></article>
<www key="homepages/5/mjf"><author>Michael J. Franklin</author><title>Home Page</title></www>
<www key="homepages/5/mf"><author>Michael Franklin</author><title>Home Page</title>
  <note type="affiliation">Some Other Place</note></www>
<article key="journals/x/AmesF25"><author>Alice Ames</author><author>Michael J. Franklin</author>
  <title>Middle name paper.</title><year>2025</year><journal>X</journal></article>
<www key="homepages/7/carol"><author>Carol Chen</author><title>Home Page</title>
  <note type="affiliation" label="until 2024">Old University</note>
  <note type="affiliation" label="since 2025">New University</note>
  <note type="affiliation" label="2015-2020">Older Institute</note></www>
<www key="homepages/w/WeiWang" publtype="disambiguation"><author>Wei Wang</author><title>Home Page</title></www>
<article key="journals/y/DW20"><author>Dan Dorsey</author><author>Wei Wang</author>
  <title>Unassigned Wei Wang paper.</title><year>2020</year><journal>Y</journal></article>
""" + "".join(
    # 30 people named Lei Zhang; only the last one coauthored with Alice.
    f'<www key="homepages/9/lz{i}"><author>Lei Zhang {i:04d}</author><title>Home Page</title></www>\n'
    for i in range(1, 31)
) + """<article key="journals/z/AmesZ25"><author>Alice Ames</author><author>Lei Zhang 0030</author>
  <title>Common name paper.</title><year>2025</year><journal>Z</journal></article>
</dblp>
"""


@pytest.fixture(scope="session")
def db(tmp_path_factory) -> DBLP:
    d = tmp_path_factory.mktemp("dblp")
    dump = d / "dblp.xml.gz"
    with gzip.open(dump, "wb") as f:
        f.write(DUMP.encode("iso-8859-1"))
    import_dump(dump, d / "dblp.sqlite")
    return DBLP(d / "dblp.sqlite")


@pytest.fixture
def home(tmp_path: Path, db: DBLP) -> Path:
    (tmp_path / "dblp.sqlite").symlink_to(db.path)
    return tmp_path
