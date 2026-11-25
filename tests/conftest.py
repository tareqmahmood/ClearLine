import gzip
from pathlib import Path

import pytest

from clearline.db import DBLP
from clearline.dblp_import import import_dump

# A tiny DBLP dump. Two different people are named "Wei Wang" (0001 and 0002),
# "J&uuml;rgen" exercises entity decoding, and "A. Alice" is an alias of Alice Ames.
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
