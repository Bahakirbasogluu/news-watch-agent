"""Article identity: the same story must produce the same key however it was linked."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_", "mc_", "pk_")
_TRACKING_NAMES = {"fbclid", "gclid", "ocid", "cmpid", "ref", "ref_src", "igshid", "yclid"}
_MIN_TITLE_WORDS = 4


def fold(text: str) -> str:
    """Lower-case with İ, I and ı mapped to i, keeping the length of the text."""
    return text.replace("İ", "i").replace("ı", "i").lower()


def canonical_url(url: str) -> str:
    """Strip what does not identify the article: tracking parameters, fragment, www, case."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_NAMES and not k.lower().startswith(_TRACKING_PREFIXES)
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", host, path, urlencode(query), ""))


def domain_of(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def dedup_key(customer_id: str, url: str) -> str:
    """Key of an article for one customer. The store enforces it as unique."""
    return f"{customer_id}:{hashlib.sha256(canonical_url(url).encode()).hexdigest()}"


def normalized_title(title: str) -> str:
    """Title reduced to its words, without the ' - Publisher' suffix aggregators append."""
    head = re.split(r"\s+[-|–—]\s+(?=[^-|–—]*$)", title.strip(), maxsplit=1)[0]
    return " ".join(re.findall(r"\w+", fold(head)))


def is_dedup_signal(normalized: str) -> bool:
    """Very short titles ("Results", "Update") repeat across unrelated stories."""
    return len(normalized.split()) >= _MIN_TITLE_WORDS
