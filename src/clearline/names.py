"""Name and institution normalization used for matching."""

from __future__ import annotations

import re
import unicodedata

_DBLP_SUFFIX = re.compile(r"\s+\d{4}$")  # "Wei Wang 0001"
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


# Letters that Unicode does not decompose into a base letter plus an accent.
_LETTERS = str.maketrans({"ß": "ss", "ø": "o", "ł": "l", "ı": "i", "đ": "d", "ð": "d",
                          "æ": "ae", "œ": "oe", "þ": "th", "ħ": "h", "ŀ": "l"})


def fold(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold().translate(_LETTERS)
    text = text.replace("-", " ").replace(".", " ").replace("'", "")
    text = _NON_ALNUM.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def strip_dblp_suffix(name: str) -> str:
    return _DBLP_SUFFIX.sub("", name.strip())


def norm_name(name: str) -> str:
    """Key used to look up people: 'Wei Wang 0001' -> 'wei wang'."""
    return fold(strip_dblp_suffix(name))


def short_key(name: str) -> str:
    """First and last token only: 'Jane Q. Doe' -> 'jane doe'. Used as a fallback."""
    tokens = norm_name(name).split()
    if len(tokens) <= 2:
        return " ".join(tokens)
    return f"{tokens[0]} {tokens[-1]}"


# Common English nicknames -> the formal name DBLP usually records.
_NICKNAMES = {
    "bob": "robert", "rob": "robert", "bobby": "robert", "bill": "william", "will": "william",
    "billy": "william", "jim": "james", "jimmy": "james", "mike": "michael", "dave": "david",
    "tom": "thomas", "tommy": "thomas", "chris": "christopher", "alex": "alexander",
    "sam": "samuel", "ben": "benjamin", "dan": "daniel", "danny": "daniel", "joe": "joseph",
    "nick": "nicholas", "matt": "matthew", "steve": "steven", "tony": "anthony",
    "andy": "andrew", "rick": "richard", "rich": "richard", "dick": "richard",
    "kate": "katherine", "katie": "katherine", "kathy": "katherine", "liz": "elizabeth",
    "beth": "elizabeth", "jen": "jennifer", "jenny": "jennifer", "jeff": "jeffrey",
    "greg": "gregory", "ed": "edward", "ted": "edward", "pat": "patrick", "tim": "timothy",
    "ron": "ronald", "don": "donald", "ken": "kenneth", "larry": "lawrence", "jon": "jonathan",
    "phil": "philip", "fred": "frederick", "frank": "francis", "hank": "henry",
    "charlie": "charles", "chuck": "charles", "sue": "susan", "susie": "susan",
    "meg": "margaret", "maggie": "margaret", "peggy": "margaret", "vicky": "victoria",
    "becky": "rebecca", "abby": "abigail", "debbie": "deborah", "deb": "deborah",
    "mandy": "amanda", "manny": "manuel", "nate": "nathan", "zach": "zachary",
    "josh": "joshua", "jake": "jacob", "max": "maximilian", "sasha": "alexander",
}


_UMLAUT_SPELLING = re.compile(r"([aou])e(?=[a-z])")


def _translit(token: str) -> str:
    """Fold German-style spellings so 'mueller' and 'muller' (from Müller) agree.

    Only inside a word, so 'doe' and 'joe' are left alone."""
    return _UMLAUT_SPELLING.sub(r"\1", token)


def loose_key(name: str) -> str:
    """A forgiving key for when the exact name finds nobody.

    Expands nicknames, folds umlaut spellings, drops middle initials, and ignores
    word order: 'Bob Q. Mueller' and 'Müller Robert' both give 'muller robert'.
    """
    tokens = norm_name(name).split()
    if not tokens:
        return ""
    if len(tokens) > 2:
        tokens = [tokens[0]] + [t for t in tokens[1:-1] if len(t) > 1] + [tokens[-1]]
    # Any position, since the order is not trusted ('Brown Bob').
    return " ".join(sorted(_translit(_NICKNAMES.get(t, t)) for t in tokens))


def initial_key(name: str) -> str:
    """'Jane Q. Doe' -> 'j doe'. Matched only when a pasted name starts with an initial."""
    tokens = norm_name(name).split()
    if len(tokens) < 2:
        return ""
    return f"{tokens[0][0]} {_translit(tokens[-1])}"


def starts_with_initial(name: str) -> bool:
    tokens = norm_name(name).split()
    return len(tokens) >= 2 and len(tokens[0]) == 1


def flip_comma_name(name: str) -> str:
    """'Doe, Jane' -> 'Jane Doe'. Leaves other names alone."""
    if name.count(",") == 1:
        last, first = (p.strip() for p in name.split(","))
        if first and last and len(first.split()) <= 3 and len(last.split()) <= 3:
            return f"{first} {last}"
    return name


# ----------------------------------------------------------------- institutions
#
# Institution names are compared as ordered word sequences, not word sets, so
# "Washington University" and "University of Washington" stay apart. Generic words
# ("university", "research") are kept for that ordering, but a match needs at least
# one distinctive word in common.

_INST_DROP = {
    "the", "of", "at", "and", "for", "in", "de", "di", "der", "des", "la", "le", "du",
    "del", "da", "fur", "inc", "ltd", "llc", "corp", "corporation", "co", "gmbh", "ag",
    "sa", "plc", "bv",
}
_INST_GENERIC = {
    "university", "institute", "college", "school", "research", "laboratory", "lab", "labs",
    "center", "centre", "technology", "technologies", "science", "sciences", "technical",
    "polytechnic", "academy", "national", "state", "computer", "computing", "engineering",
    "department", "faculty", "group", "company", "foundation",
}
# Abbreviations expanded word by word ("MIT CSAIL" -> "massachusetts institute ...").
_INST_WORDS = {
    "univ": "university", "uni": "university", "universitat": "university",
    "universite": "university", "universidad": "university", "universita": "university",
    "universiteit": "university", "inst": "institute", "tech": "technology",
    "tu": "technical university", "mit": "massachusetts institute technology",
    "cmu": "carnegie mellon university", "msr": "microsoft research",
    "unsw": "university new south wales", "uw": "university washington",
    "nus": "national university singapore", "ntu": "nanyang technological university",
    "hku": "university hong kong", "hkust": "hong kong university science technology",
    "cuhk": "chinese university hong kong", "kaist": "korea advanced institute science technology",
    "epfl": "ecole polytechnique federale lausanne", "iisc": "indian institute science",
    "tum": "technical university munich", "uiuc": "university illinois urbana champaign",
    "ucla": "uc los angeles", "ucsd": "uc san diego", "ucsb": "uc santa barbara",
    "uci": "uc irvine", "ucb": "uc berkeley", "ucd": "uc davis",
}
# Whole-name aliases that word-by-word expansion cannot express.
_INST_PHRASES = {
    "uw madison": "university wisconsin madison",
    "georgia tech": "georgia institute technology",
    "virginia tech": "virginia polytechnic institute state university",
    "eth": "eth zurich",
    "tu munich": "technical university munich",
}
# Companies: different labs or sites of one company are "related".
_COMPANIES = {
    "google", "microsoft", "amazon", "meta", "facebook", "ibm", "oracle", "alibaba", "huawei",
    "tencent", "baidu", "bytedance", "databricks", "snowflake", "sap", "intel", "nvidia",
    "apple", "adobe", "salesforce", "samsung", "nec", "ntt", "hitachi", "fujitsu", "yahoo",
    "linkedin", "uber", "twitter", "netflix", "vmware", "cisco", "hp", "hpe", "siemens",
}

_LOCATION = {
    "usa", "us", "uk", "united", "states", "kingdom", "america", "china", "prc", "germany",
    "france", "italy", "spain", "canada", "australia", "india", "japan", "korea", "south",
    "singapore", "switzerland", "netherlands", "the", "sweden", "denmark", "norway",
    "finland", "austria", "belgium", "israel", "brazil", "greece", "portugal", "ireland",
    "taiwan", "hong", "kong", "sar", "new", "zealand", "qatar", "uae", "saudi", "arabia",
    "bangladesh", "pakistan", "iran", "turkey", "poland", "czech", "republic", "russia",
    # US state codes, which DBLP addresses often include ("Madison, WI, USA").
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id", "il", "in", "ia",
    "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj",
    "nm", "ny", "nc", "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt",
    "va", "wa", "wv", "wi", "wy", "dc",
}
_DEPT = re.compile(r"^\s*(dept|department|school|faculty|division|group|chair|lab|laboratory)\b",
                   re.IGNORECASE)
_UC = re.compile(r"\buniversity of california\s*,?\s*", re.IGNORECASE)


def _inst_words(segment: str) -> tuple[str, ...]:
    text = fold(segment)
    text = _INST_PHRASES.get(text, text)
    words = []
    for w in text.split():
        words.extend(_INST_WORDS.get(w, w).split())
    text = " ".join(w for w in words if w not in _INST_DROP and not w.isdigit())
    return tuple(_INST_PHRASES.get(text, text).split())


def inst_segments(text: str) -> list[tuple[str, ...]]:
    """Comma-separated parts of an institution, minus departments and pure locations.

    'Dept. of CS, Stanford University, Stanford, CA 94305, USA'
        -> [('stanford', 'university'), ('stanford',)]
    """
    text = _UC.sub("UC ", text)  # "University of California, Berkeley" -> "UC Berkeley"
    out = []
    for seg in text.split(","):
        if not seg.strip() or _DEPT.match(seg):
            continue
        if {w for w in fold(seg).split() if not w.isdigit()} <= _LOCATION:
            continue  # "USA", "CA 94305"
        words = _inst_words(seg)
        if words:
            out.append(words)
    return out


def _distinctive(words: tuple[str, ...]) -> bool:
    return any(w not in _INST_GENERIC for w in words)


def _is_prefix(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
    return len(short) < len(long) and long[:len(short)] == short and _distinctive(short)


def inst_match(a: str, b: str) -> str | None:
    """'same', 'related', or None.

    same:    the main part of the names agree ("UCLA" and "University of California,
             Los Angeles, USA"; "Stanford University" and "Stanford University, Stanford, CA").
    related: one is a unit or site of the other ("MIT CSAIL" and "MIT"; "Microsoft
             Research Asia" and "Microsoft"; "University of Wisconsin" and
             "University of Wisconsin-Madison"). Reported as a potential conflict.
    """
    if not a or not b:
        return None
    sa, sb = inst_segments(a), inst_segments(b)
    if not sa or not sb:
        return None
    pa, pb = sa[0], sb[0]
    if pa == pb and _distinctive(pa):
        return "same"
    wa = tuple(w for s in sa for w in s)
    wb = tuple(w for s in sb for w in s)
    if _is_prefix(pa, pb) or _is_prefix(pb, pa) or _is_prefix(wa, wb) or _is_prefix(wb, wa):
        return "related"
    if pa[0] in _COMPANIES and pa[0] == pb[0]:
        return "related"
    if any(x == y and _distinctive(x) for x in sa for y in sb):
        return "related"
    return None


def same_institution(a: str, b: str) -> bool:
    return inst_match(a, b) == "same"


def inst_score(a: str, b: str) -> float:
    """For choosing between people who share a name: 1 same, 0.7 related, 0 otherwise."""
    return {"same": 1.0, "related": 0.7}.get(inst_match(a, b), 0.0)


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""


def domain_matches(domain: str, owned: str) -> bool:
    """'cs.wisc.edu' matches 'wisc.edu'; 'gmail.com' never counts."""
    domain, owned = domain.lower().strip("."), owned.lower().strip(".")
    if not domain or not owned or owned in PUBLIC_EMAIL_DOMAINS:
        return False
    return domain == owned or domain.endswith("." + owned)


PUBLIC_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "icloud.com", "me.com", "protonmail.com", "proton.me",
    "qq.com", "163.com", "126.com", "foxmail.com", "aol.com", "gmx.de",
}
