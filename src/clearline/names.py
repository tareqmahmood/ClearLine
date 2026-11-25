"""Name and institution normalization used for matching."""

from __future__ import annotations

import re
import unicodedata

_DBLP_SUFFIX = re.compile(r"\s+\d{4}$")  # "Wei Wang 0001"
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def fold(text: str) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold().replace("ß", "ss").replace("ø", "o").replace("ł", "l")
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


_INST_STOP = {
    "the", "of", "at", "and", "for", "in", "de", "di", "der", "des", "la", "le",
    "university", "univ", "universitat", "universite", "universidad", "universita",
    "institute", "inst", "college", "school", "department", "dept", "lab", "labs",
    "laboratory", "laboratories", "research", "center", "centre", "inc", "ltd",
    "llc", "corp", "corporation", "company", "co", "gmbh", "technology",
    "technologies", "science", "sciences", "computer", "computing", "national",
}

_INST_ALIASES = {
    "uw madison": "university of wisconsin madison",
    "uiuc": "university of illinois urbana champaign",
    "cmu": "carnegie mellon university",
    "mit": "massachusetts institute of technology",
    "ucb": "university of california berkeley",
    "uc berkeley": "university of california berkeley",
    "ucla": "university of california los angeles",
    "ucsd": "university of california san diego",
    "nus": "national university of singapore",
    "ntu": "nanyang technological university",
    "hkust": "hong kong university of science and technology",
    "cuhk": "chinese university of hong kong",
    "epfl": "ecole polytechnique federale de lausanne",
    "eth zurich": "eth zurich",
    "tum": "technical university of munich",
    "kaist": "korea advanced institute of science and technology",
    "iisc": "indian institute of science",
    "msr": "microsoft research",
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
_DEPT = re.compile(r"^\s*(dept|department|school|faculty|division|group|chair)\b", re.IGNORECASE)


def inst_tokens(text: str) -> set[str]:
    """Distinctive tokens of an institution name, without departments or locations."""
    folded = fold(text)
    if folded in _INST_ALIASES:
        folded = _INST_ALIASES[folded]
    else:
        segments = [s for s in text.split(",") if s.strip() and not _DEPT.match(s)]
        kept = []
        for s in segments:
            f = fold(s)
            f = _INST_ALIASES.get(f, f)
            words = {w for w in f.split() if not w.isdigit()}  # drop ZIP codes
            if words <= _LOCATION:  # a segment that is only a place: "USA", "CA 94305"
                continue
            kept.append(f)
        folded = " ".join(kept) or folded
    return {t for t in folded.split()
            if t not in _INST_STOP and not t.isdigit() and len(t) > 1}


def inst_similarity(a: str, b: str) -> float:
    """How alike two institution names are, 0..1.

    Jaccard similarity of distinctive tokens, raised to 1.0 when one name's tokens are all
    in the other and there are at least two of them ("Wisconsin Madison" inside
    "Wisconsin Madison Madison"). A single shared token such as "california" is not
    enough on its own, so UC Berkeley and UCLA stay apart.
    """
    ta, tb = inst_tokens(a), inst_tokens(b)
    if not ta or not tb:
        return 0.0
    small, big = sorted((ta, tb), key=len)
    if len(small) >= 2 and small <= big:
        return 1.0
    return len(ta & tb) / len(ta | tb)


def same_institution(a: str, b: str, threshold: float = 0.6) -> bool:
    if not a or not b:
        return False
    if fold(a) == fold(b):
        return True
    return inst_similarity(a, b) >= threshold


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
