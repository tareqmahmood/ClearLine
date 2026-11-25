"""Parse reviewer / PC member lists pasted from CMT, EasyChair, OpenReview, or a CSV.

Each line is one person. Recognised shapes include:
    Jane Doe
    Jane Doe (University of Somewhere)
    Jane Doe, University of Somewhere
    Jane Doe - University of Somewhere
    Jane Doe <jane@somewhere.edu>
    Doe, Jane
    Jane<TAB>Doe<TAB>jane@somewhere.edu<TAB>University of Somewhere   (copied table rows)
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

from .names import flip_comma_name

EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
ORG_WORDS = re.compile(
    r"\b(univ\w*|institut\w*|college|school|lab\w*|research|center|centre|inc|ltd|llc|"
    r"corp\w*|gmbh|company|technolog\w*|polytechn\w*|academy|hospital|foundation|"
    r"google|microsoft|amazon|meta|ibm|oracle|alibaba|huawei|tencent|databricks|"
    r"snowflake|sap|intel|nvidia|apple|bytedance|baidu|adobe|salesforce|"
    r"ETH|EPFL|MIT|CMU|KAIST|INRIA|CNRS|CWI|MPI|NUS|NTU|HKUST|IISc|IIT)\b",
    re.IGNORECASE)
HEADER_WORDS = {"name", "first name", "last name", "email", "e-mail", "organization",
                "organisation", "affiliation", "institution", "conflict", "conflicts",
                "select", "reviewer", "role", "country", "domain"}
CHECKBOX = re.compile(r"^[\s☐☑✓✔□■\[\]xX*•·-]+(?=\S)")


@dataclass
class PCMember:
    raw: str
    name: str
    institution: str = ""
    email: str = ""

    def to_dict(self) -> dict:
        return {"raw": self.raw, "name": self.name, "institution": self.institution,
                "email": self.email}


def _looks_like_org(s: str) -> bool:
    return bool(ORG_WORDS.search(s)) or len(s.split()) > 4


def _looks_like_name_part(s: str) -> bool:
    return bool(s) and not _looks_like_org(s) and "@" not in s and \
        not any(c.isdigit() for c in s) and len(s.split()) <= 4


def _clean(s: str) -> str:
    return s.strip().strip('"').strip()


def parse_line(line: str) -> PCMember | None:
    raw = line.rstrip("\n")
    text = CHECKBOX.sub("", raw).strip()
    if not text or text.casefold() in HEADER_WORDS:
        return None
    if "\t" in text or text.count(";") >= 2:
        return _parse_fields(raw, re.split(r"\t|;", text))
    if text.count(",") >= 2 and EMAIL.search(text):  # CSV-like row
        return _parse_fields(raw, next(csv.reader([text])))

    email = ""
    m = EMAIL.search(text)
    if m:
        email = m.group(0)
        text = (text[:m.start()] + text[m.end():]).replace("<>", "").replace("()", "").strip(" ,;-")

    institution = ""
    m = re.match(r"^(.*?)\s*[(\[](.+)[)\]]\s*$", text)  # Name (Institution)
    if m:
        text, institution = m[1], m[2]
    elif re.search(r"\s[-–—|]\s", text):  # Name - Institution
        text, institution = re.split(r"\s[-–—|]\s", text, maxsplit=1)
    elif "," in text:
        head, tail = (p.strip() for p in text.split(",", 1))
        if _looks_like_org(tail) or len(tail.split()) > 2:
            text, institution = head, tail  # Name, Institution
        else:
            text = flip_comma_name(text)  # Doe, Jane
    name = _clean(text)
    if not name or name.casefold() in HEADER_WORDS:
        return None
    return PCMember(raw=raw, name=name, institution=_clean(institution), email=email)


def _parse_fields(raw: str, fields: list[str]) -> PCMember | None:
    fields = [_clean(f) for f in fields]
    fields = [f for f in fields if f and f.casefold() not in HEADER_WORDS]
    if not fields:
        return None
    email = next((f for f in fields if EMAIL.fullmatch(f)), "")
    rest = [f for f in fields if f != email]
    name_parts: list[str] = []
    institution = ""
    for f in rest:
        if not institution and name_parts and _looks_like_org(f):
            institution = f
        elif not institution and len(name_parts) < 2 and _looks_like_name_part(f) and \
                (not name_parts or len(name_parts[0].split()) == 1):
            name_parts.append(f)  # "Jane" + "Doe" from separate columns
        elif not institution and name_parts:
            institution = f
    if not name_parts:
        name_parts = rest[:1]
    name = flip_comma_name(" ".join(name_parts))
    return PCMember(raw=raw, name=name, institution=institution, email=email)


def parse(text: str) -> list[PCMember]:
    out, seen = [], set()
    for line in io.StringIO(text):
        member = parse_line(line)
        if member is None:
            continue
        key = (member.name.casefold(), member.email.casefold())
        if key not in seen:
            seen.add(key)
            out.append(member)
    return out
