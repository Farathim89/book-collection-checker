"""What you own (Owned), what a series has (Volume), and the two put together (Series)."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass, field

from .text import fold

AUDIO, EBOOK = "audio", "ebook"
KINDS = ("audiobook", "light novel", "ebook", "manga", "comic")
_NOISE_RE = re.compile(r"(?i)^(?:the|an?)\s+|\s+series$|\s*\((?:light novel|novel|manga|comic)\)$|\s+light novel$")


def series_key(name: str, manga: bool = False) -> str:
    """'The Saga of Tanya the Evil (Manga)' -> 'sagaoftanyatheevil|manga': audio and ebook volumes of one
    novel series meet, its manga stays apart."""
    prev = None
    while prev != name:
        prev, name = name, _NOISE_RE.sub("", name).strip()
    return fold(name) + ("|manga" if manga else "")


def fmt_index(i: float | None) -> str:
    return "" if i is None else (f"{i:g}")


@dataclass
class Owned:
    title: str
    authors: list[str]
    series: str | None
    index: float | None
    fmt: str            # AUDIO / EBOOK
    kind: str           # one of KINDS
    source: str         # 'ABS: Light Novels', 'Folder', 'Staging'
    path: str = ""
    asin: str = ""
    finished: bool = False
    progress: float = 0.0
    item_id: str = ""   # Audiobookshelf's id (for its cover)

    @property
    def author(self) -> str:
        return self.authors[0] if self.authors else ""


@dataclass
class Volume:
    index: float
    title: str = ""
    release: str = ""   # 'YYYY-MM-DD'
    asin: str = ""
    url: str = ""
    source: str = ""    # 'audible' / 'anilist' / 'google'
    cover: str = ""     # image URL

    @property
    def upcoming(self) -> bool:
        return bool(self.release) and self.release > dt.date.today().isoformat()


@dataclass
class Series:
    key: str
    name: str
    author: str
    kind: str
    owned: list[Owned] = field(default_factory=list)
    audible: dict[float, Volume] = field(default_factory=dict)   # what Audible sells (audio)
    others: dict[float, Volume] = field(default_factory=dict)    # AniList / Google (books)
    total_hint: int | None = None   # AniList: number of volumes
    status: str = ""                # AniList: FINISHED / RELEASING
    links: dict[str, str] = field(default_factory=dict)          # source -> page
    checked: str = ""               # when looked up online
    track: list[str] = field(default_factory=list)  # formats you collect it in ([] = the ones you own)

    # -- what you have ------------------------------------------------------
    def index_of(self, o: Owned) -> float | None:
        """Your copy's number - Audible's, when the ASIN is the same ('6.1' Part One is Audible's #6)."""
        if o.asin:
            hit = next((i for i, v in self.audible.items() if v.asin and v.asin == o.asin), None)
            if hit is not None:
                return hit
        return o.index

    def have(self, fmt: str) -> set[float]:
        return {i for o in self.owned if o.fmt == fmt and (i := self.index_of(o)) is not None}

    @property
    def formats(self) -> set[str]:
        """The formats you collect this series in: your choice, else the ones you own."""
        return set(self.track) if self.track else {o.fmt for o in self.owned}

    # -- what exists ----------------------------------------------------------
    def known(self, fmt: str) -> dict[float, Volume]:
        """Volumes that exist (released or announced) for this format."""
        if fmt == AUDIO:
            return dict(self.audible)
        out = dict(self.others)
        for i, v in self.audible.items():  # an audiobook volume exists as a book too
            out.setdefault(i, v)
        owned = [i for o in self.owned if (i := self.index_of(o)) is not None]
        hint = self._whole_hint(out, owned)
        top = max([*out, float(hint), *owned], default=0)
        for n in range(1, int(top) + 1):  # 1..N: AniList knows the count, a gap in yours shows too
            out.setdefault(float(n), Volume(float(n), source="anilist" if n <= hint else "gap"))
        return out

    def _whole_hint(self, known, owned) -> int:
        """AniList's count as numbered volumes. It counts side volumes too ('Classroom of the Elite (Year 2)':
        15 = 12 + 4.5, 9.5, 12.5) - with side volumes in the series it can't say which number is missing,
        so it adds no rows then (anilist_unlisted tells how many it knows more)."""
        if any(i != int(i) for i in [*known, *owned]):
            return 0
        return self.total_hint or 0

    def anilist_unlisted(self, fmt: str) -> int:
        """How many volumes AniList counts that aren't in this list (e.g. a .5 volume nobody sells yet)."""
        return max(0, (self.total_hint or 0) - len(self.known(fmt)))

    def missing(self, fmt: str) -> list[Volume]:
        """Released volumes you don't have - only for a format you collect this series in."""
        if fmt not in self.formats:
            return []
        have = self.have(fmt)
        return [v for i, v in sorted(self.known(fmt).items()) if i not in have and not v.upcoming]

    def upcoming(self) -> list[Volume]:
        seen = {**self.others, **self.audible}
        return [v for _, v in sorted(seen.items()) if v.upcoming]

    @property
    def missing_count(self) -> int:
        return sum(len(self.missing(f)) for f in (AUDIO, EBOOK))

    @property
    def next_release(self) -> Volume | None:
        ups = self.upcoming()
        return min(ups, key=lambda v: v.release) if ups else None

    def to_json(self) -> dict:
        d = asdict(self)
        d["audible"] = {fmt_index(k): asdict(v) for k, v in self.audible.items()}
        d["others"] = {fmt_index(k): asdict(v) for k, v in self.others.items()}
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Series":
        s = cls(d["key"], d["name"], d["author"], d["kind"])
        s.owned = [Owned(**o) for o in d.get("owned", [])]
        s.audible = {float(k): Volume(**v) for k, v in (d.get("audible") or {}).items()}
        s.others = {float(k): Volume(**v) for k, v in (d.get("others") or {}).items()}
        s.total_hint, s.status = d.get("total_hint"), d.get("status", "")
        s.links, s.checked = d.get("links") or {}, d.get("checked", "")
        s.track = list(d.get("track") or [])
        return s
