"""Download the DBLP XML dump and import it into a compact SQLite database.

Only what conflict checking needs is kept: who wrote what and when, plus
DBLP's person records (name aliases and affiliations).
"""

from __future__ import annotations

import gzip
import html.entities
import os
import sqlite3
import sys
import time
import urllib.request
import xml.parsers.expat
from pathlib import Path

DUMP_URL = "https://dblp.org/xml/dblp.xml.gz"

# Records whose <author> elements mean "these people wrote this together".
# Proceedings volumes list editors, not coauthors, so they are skipped.
PUB_TAGS = {
    "article", "inproceedings", "incollection", "book",
    "phdthesis", "mastersthesis",
}
RECORD_TAGS = PUB_TAGS | {"proceedings", "www", "data"}
TEXT_TAGS = {"author", "title", "year", "journal", "booktitle", "note"}

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE persons(id INTEGER PRIMARY KEY, pid TEXT, name TEXT NOT NULL);
CREATE TABLE names(
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, norm TEXT NOT NULL,
    short TEXT NOT NULL, person_id INTEGER);
CREATE TABLE affiliations(person_id INTEGER NOT NULL, text TEXT NOT NULL, former INTEGER NOT NULL);
CREATE TABLE pubs(
    id INTEGER PRIMARY KEY, key TEXT NOT NULL, year INTEGER, title TEXT,
    venue TEXT, n_authors INTEGER NOT NULL);
CREATE TABLE authorship(pub_id INTEGER NOT NULL, name_id INTEGER NOT NULL);
"""

INDEXES = """
CREATE INDEX names_norm ON names(norm);
CREATE INDEX names_short ON names(short);
CREATE INDEX names_person ON names(person_id);
CREATE INDEX persons_pid ON persons(pid);
CREATE INDEX aff_person ON affiliations(person_id);
CREATE INDEX auth_name ON authorship(name_id, pub_id);
CREATE INDEX auth_pub ON authorship(pub_id, name_id);
"""


def _progress(msg: str) -> None:
    sys.stderr.write("\r" + msg.ljust(78))
    sys.stderr.flush()


def download(dest: Path, url: str = DUMP_URL) -> Path:
    """Download the dump to dest, resuming a partial download if present."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "ClearLine (COI checker)"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    with urllib.request.urlopen(req) as resp:
        if have and resp.status != 206:  # server ignored the range: start over
            have = 0
        total = int(resp.headers.get("Content-Length", 0)) + have
        mode = "ab" if have else "wb"
        done = have
        start = time.time()
        with open(part, mode) as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                rate = (done - have) / max(time.time() - start, 1e-6) / 1e6
                pct = f"{100 * done / total:5.1f}%" if total else ""
                _progress(f"Downloading DBLP: {done / 1e6:,.0f} MB {pct} ({rate:.1f} MB/s)")
    sys.stderr.write("\n")
    part.replace(dest)
    return dest


class _DumpParser:
    """Streams dblp.xml with expat and writes rows in batches."""

    BATCH = 50_000

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.name_ids: dict[str, int] = {}
        self.persons: list[tuple[int, str, str]] = []  # (id, pid, name)
        self.alias_of: dict[str, int] = {}  # name -> person id
        self.affs: list[tuple[int, str, int]] = []
        self.pubs: list[tuple] = []
        self.auth: list[tuple[int, int]] = []
        self.pub_id = 0
        self.n_records = 0
        self._reset()

    def _reset(self) -> None:
        self.tag = None
        self.attrs: dict = {}
        self.authors: list[str] = []
        self.fields: dict[str, str] = {}
        self.notes: list[tuple[str, dict]] = []
        self.field = None
        self.field_attrs: dict = {}
        self.buf: list[str] = []

    # expat callbacks ------------------------------------------------------
    def start(self, tag, attrs):
        if self.tag is None:
            if tag in RECORD_TAGS:
                self.tag, self.attrs = tag, attrs
        elif self.field is None and tag in TEXT_TAGS:
            self.field, self.field_attrs, self.buf = tag, attrs, []

    def end(self, tag):
        if self.tag is None:
            return
        if tag == self.field:
            text = "".join(self.buf).strip()
            if tag == "author":
                self.authors.append(text)
            elif tag == "note":
                self.notes.append((text, self.field_attrs))
            else:
                self.fields.setdefault(tag, text)
            self.field = None
        elif tag == self.tag:
            self._record()
            self._reset()

    def chars(self, data):
        if self.field is not None:
            self.buf.append(data)

    def skipped(self, name, is_param):
        # DBLP uses HTML entity names (&uuml; etc.) declared in dblp.dtd.
        if self.field is not None and not is_param:
            cp = html.entities.name2codepoint.get(name)
            self.buf.append(chr(cp) if cp else f"&{name};")

    # record handling ------------------------------------------------------
    def _name_id(self, name: str) -> int:
        nid = self.name_ids.get(name)
        if nid is None:
            nid = self.name_ids[name] = len(self.name_ids) + 1
        return nid

    def _record(self):
        self.n_records += 1
        key = self.attrs.get("key", "")
        if self.tag == "www":
            if key.startswith("homepages/") and self.authors and \
                    self.attrs.get("publtype") != "disambiguation":
                person_id = len(self.persons) + 1
                pid = key.removeprefix("homepages/")
                self.persons.append((person_id, pid, self.authors[0]))
                for alias in self.authors:
                    self.alias_of.setdefault(alias, person_id)
                for text, attrs in self.notes:
                    if attrs.get("type") == "affiliation" and text:
                        former = 1 if attrs.get("label") == "former" else 0
                        self.affs.append((person_id, text, former))
        elif self.tag in PUB_TAGS and self.authors:
            self.pub_id += 1
            year = self.fields.get("year")
            venue = self.fields.get("journal") or self.fields.get("booktitle") or ""
            self.pubs.append((
                self.pub_id, key, int(year) if year and year.isdigit() else None,
                self.fields.get("title", ""), venue, len(self.authors),
            ))
            for name in dict.fromkeys(self.authors):  # dedupe, keep order
                self.auth.append((self.pub_id, self._name_id(name)))
        if len(self.auth) >= self.BATCH * 4 or len(self.pubs) >= self.BATCH:
            self.flush()

    def flush(self):
        c = self.conn
        if self.pubs:
            c.executemany("INSERT INTO pubs VALUES (?,?,?,?,?,?)", self.pubs)
            self.pubs.clear()
        if self.auth:
            c.executemany("INSERT INTO authorship VALUES (?,?)", self.auth)
            self.auth.clear()

    def finish(self):
        """Write names and persons once every record has been seen."""
        from .names import norm_name, short_key

        self.flush()
        c = self.conn
        _progress("Linking names to DBLP person records...")
        # Names that never appear on a publication still matter as aliases.
        for alias in self.alias_of:
            self._name_id(alias)
        next_person = len(self.persons) + 1
        rows = []
        for name, nid in self.name_ids.items():
            person_id = self.alias_of.get(name)
            if person_id is None:  # no homepage record: the name is the person
                person_id = next_person
                next_person += 1
                self.persons.append((person_id, None, name))
            rows.append((nid, name, norm_name(name), short_key(name), person_id))
            if len(rows) >= self.BATCH * 4:
                c.executemany("INSERT INTO names VALUES (?,?,?,?,?)", rows)
                rows.clear()
        c.executemany("INSERT INTO names VALUES (?,?,?,?,?)", rows)
        c.executemany("INSERT INTO persons VALUES (?,?,?)", self.persons)
        c.executemany("INSERT INTO affiliations VALUES (?,?,?)", self.affs)


def import_dump(dump: Path, db_path: Path) -> dict:
    """Parse dump (dblp.xml or dblp.xml.gz) into a fresh SQLite database at db_path."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".importing")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    conn.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-200000;")
    conn.executescript(SCHEMA)

    handler = _DumpParser(conn)
    parser = xml.parsers.expat.ParserCreate()
    parser.buffer_text = True
    parser.buffer_size = 1 << 20
    parser.StartElementHandler = handler.start
    parser.EndElementHandler = handler.end
    parser.CharacterDataHandler = handler.chars
    parser.SkippedEntityHandler = handler.skipped
    parser.SetParamEntityParsing(xml.parsers.expat.XML_PARAM_ENTITY_PARSING_NEVER)

    size = os.path.getsize(dump)
    start = time.time()
    with open(dump, "rb") as raw:
        stream = gzip.GzipFile(fileobj=raw) if str(dump).endswith(".gz") else raw
        while chunk := stream.read(1 << 22):
            parser.Parse(chunk, False)
            pct = 100 * raw.tell() / size
            _progress(f"Importing DBLP: {pct:5.1f}%  {handler.pub_id:,} publications  "
                      f"{time.time() - start:,.0f}s")
        parser.Parse(b"", True)
    handler.finish()

    _progress("Building indexes (a few minutes)...")
    conn.executescript(INDEXES)
    stats = {
        "source": str(dump),
        "imported_at": time.strftime("%Y-%m-%d %H:%M"),
        "dump_modified": time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(dump))),
        "publications": handler.pub_id,
        "names": len(handler.name_ids),
        "persons": len(handler.persons),
        "seconds": round(time.time() - start),
    }
    conn.executemany("INSERT INTO meta VALUES (?,?)", [(k, str(v)) for k, v in stats.items()])
    conn.commit()
    conn.execute("ANALYZE")
    conn.close()
    tmp.replace(db_path)
    sys.stderr.write("\n")
    return stats
