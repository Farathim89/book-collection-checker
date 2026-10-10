"""What a series has, online:
    Audible  - every volume it sells (number, title, release date - also upcoming ones)
    AniList  - how many volumes a light novel / manga has, and if it is finished
    Google   - ebook volumes Audible doesn't sell (numbers found in the titles)
Answers are cached for a week (providers.http)."""
from __future__ import annotations

import datetime as dt
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from ..config import Settings
from ..providers.http import Cache, Http
from .models import AUDIO, Series, Volume
from .text import fold

TLDS = {"us": "com", "uk": "co.uk", "ca": "ca", "au": "com.au", "de": "de", "fr": "fr", "jp": "co.jp",
        "in": "in", "it": "it", "es": "es"}
_PLACEHOLDER = "2200"  # Audible's date for listings that aren't for sale
_VOL_RE = re.compile(r"(?i)(?:\bvol(?:ume)?\.?|\bbook|\bpart|#|,)\s*(\d+(?:\.\d+)?)\b|\s(\d{1,3})\s*$")


def _series_key_loose(name: str) -> str:
    return fold(re.sub(r"(?i)\s+series$|\s*\((?:light novel|novel|manga)\)|^(?:the|an?)\s+", "", name or ""))


def _norm(text: str) -> str:
    """Folded, without 'the' / 'a' / 'an' and shop tags - the same on both sides of a comparison."""
    return fold(re.sub(r"(?i)\b(?:the|an?)\b|\((?:light novel|novel|manga)\)", " ", text or ""))


def _title_names(p: dict, series_name: str) -> bool:
    """The product's title names this series: 'Head: Tail' -> the tail is in it ('Trapped in a Dating Sim:
    Otome Games Are Tough for Us, Too!' -> 'otomegamesaretoughforustoo'), else the whole name."""
    core = _norm((series_name or "").split(":", 1)[-1])
    return bool(core) and core in _norm(f"{p.get('title') or ''} {p.get('subtitle') or ''}")


def _names_other_series(p: dict, series_asin: str, series_name: str) -> bool:
    """Audible lists a sibling's volume under this series: the title starts with the shared 'Head:' but goes on
    with another series ('Trapped in a Dating Sim: The World of Otome Games Is Tough for Mobs, Vol. 7' under
    'Trapped in a Dating Sim: Otome Games Are Tough for Us, Too!')."""
    if ":" not in (series_name or ""):
        # 'Classroom of the Elite' (the whole franchise on Audible) holding 'Classroom of the Elite: Year 2,
        # Vol. 11' - a numbered year / part / season the series name doesn't have is another series
        title = (p.get("title") or "").strip()
        if title.lower().startswith((series_name or "").lower()):
            rest = title[len(series_name):]
            return bool(re.match(r"(?i)^\s*[:\-–(]\s*(?:year|part|season|arc|book)\s*\d", rest))
        return False
    if _title_names(p, series_name):
        return False
    head_text, tail_text = series_name.split(":", 1)
    head, core = _norm(head_text), _norm(tail_text)
    title = _norm(f"{p.get('title') or ''} {p.get('subtitle') or ''}")
    if not head or not core or not title.startswith(head) or len(title) <= len(head) + 8:
        return False
    rest = title[len(head):len(head) + len(core)]
    if re.findall(r"\d+", core) and re.findall(r"\d+", core) != re.findall(r"\d+", rest):
        return True  # 'Year 3' under 'Year 2'
    # a typo ('Jobless Reincarnatio') is still this series; another name ('The World of ... for Mobs') isn't
    return SequenceMatcher(None, core, rest).ratio() < 0.75


class Lookup:
    def __init__(self, settings: Settings, state: Path):
        cache = Cache(state / "cache.sqlite")
        self.settings = settings
        self.audible = Http(cache, min_interval=0.3)
        self.anilist = Http(cache, min_interval=2.2)  # AniList allows about 30 a minute now
        self.google = Http(cache, min_interval=1.2)
        self.google_busy = False  # Google said 'too many requests': no more Google this round
        tld = TLDS.get(settings.audible_region, "com")
        self.api = f"https://api.audible.{tld}/1.0/catalog/products"
        self.site = f"https://www.audible.{tld}"

    def check(self, s: Series) -> None:
        """Fill the series' known volumes from every source that is on."""
        notes = []
        if self.settings.use_audible and (AUDIO in s.formats or s.kind in ("audiobook", "light novel")):
            try:
                self.audible_series(s)
            except (OSError, ValueError, KeyError) as e:
                notes.append(f"Audible: {e}")
        if self.settings.use_anilist and s.kind in ("light novel", "manga"):
            try:
                self.anilist_volumes(s)
                if s.kind == "light novel" and not s.key.endswith("|manga"):
                    self.anilist_manga(s)  # is there a manga of it (to show, even when you have none)
            except (OSError, ValueError, KeyError) as e:
                notes.append(f"AniList: {e}")
        if self.settings.use_google and "ebook" in s.formats and not s.audible and not s.total_hint                 and not self.google_busy:
            try:
                self.google_volumes(s)
            except (OSError, ValueError, KeyError) as e:
                if "429" in str(e):
                    self.google_busy = True  # quietly: the next check asks again
                else:
                    notes.append(f"Google: {e}")
        s.checked = dt.datetime.now().isoformat(timespec="minutes")
        if notes:
            s.links["errors"] = "; ".join(notes)
        else:
            s.links.pop("errors", None)

    # -- Audible -------------------------------------------------------------------------------
    def audible_series(self, s: Series) -> None:
        series_asin = self._find_audible_series(s)
        if not series_asin:
            return
        products = self._series_products(series_asin)
        name = next((x.get("title") for p in products for x in p.get("series") or []
                     if x.get("asin") == series_asin and x.get("title")), s.name)
        volumes: dict[float, Volume] = {}
        for p in products:
            if _names_other_series(p, series_asin, name):
                continue  # Audible lists a main-series volume under its spin-off too
            self._add_volume(volumes, p, series_asin)
        # ... and lists some volumes only under the other series: take the ones whose title names this one
        siblings = list(dict.fromkeys(x["asin"] for p in products for x in p.get("series") or []
                                      if x.get("asin") and x["asin"] != series_asin))
        for sib in siblings[:3]:
            for p in self._series_products(sib):
                if _title_names(p, name):
                    self._add_volume(volumes, p, sib, only_new=True)
        # ... or under the whole franchise / no series at all ('Classroom of the Elite: Year 2, Vol. 11' = the
        # franchise's #27, Vol. 12.5 in none): a title search finds them - the full series name in the title
        known = {v.asin for v in volumes.values()}
        found = self.audible.get_json(self.api, {
            "title": name, "author": s.author, "num_results": 50, "products_sort_by": "Relevance",
            "response_groups": "product_desc,series,product_attrs,media", "image_sizes": "500"}).get("products") or []
        want = _norm(name)
        for p in found:
            title = f"{p.get('title') or ''} {p.get('subtitle') or ''}"
            if p.get("asin") in known or not want or want not in _norm(title):
                continue
            m = re.search(r"(?i)\bvol(?:ume)?\.?\s*(\d+(?:\.\d+)?)", title)
            if m and not _names_other_series(p, series_asin, name):
                self._add_volume(volumes, p, series_asin, only_new=True, index=float(m.group(1)))
        s.audible = volumes
        s.links["Audible"] = f"{self.site}/series/{series_asin}"

    def _series_products(self, series_asin: str) -> list[dict]:
        data = self.audible.get_json(f"{self.api}/{series_asin}", {"response_groups": "relationships,product_desc"})
        product = data.get("product") or {}
        kids = [r["asin"] for r in product.get("relationships") or []
                if r.get("relationship_to_product") == "child" and r.get("relationship_type") == "series"]
        out = []
        for i in range(0, len(kids), 50):
            res = self.audible.get_json(self.api, {"asins": ",".join(kids[i:i + 50]),
                                                   "response_groups": "product_desc,series,product_attrs,media",
                                                   "image_sizes": "500"})
            out += res.get("products") or []
        return out

    def _add_volume(self, volumes: dict[float, Volume], p: dict, series_asin: str, only_new: bool = False,
                    index: float | None = None) -> None:
        seq = next((x.get("sequence") for x in p.get("series") or [] if x.get("asin") == series_asin), None)
        if index is None:
            try:
                index = float(str(seq).split("-")[0])
            except (TypeError, ValueError):
                return
        if only_new and seq is not None:  # from another series' list: its number there ('#27') isn't ours - the title's 'Vol. 11' is
            m = re.search(r"(?i)\bvol(?:ume)?\.?\s*(\d+(?:\.\d+)?)", f"{p.get('title') or ''} {p.get('subtitle') or ''}")
            if m:
                index = float(m.group(1))
        release = (p.get("release_date") or "")[:10]
        if release.startswith(_PLACEHOLDER) or (p.get("language") or "english").lower() != "english" \
                and self.settings.audible_region in ("us", "uk", "ca", "au", "in"):
            return
        if only_new and index in volumes:
            return
        v = Volume(index, p.get("title") or "", release, p.get("asin") or "",
                   f"{self.site}/pd/{p.get('asin')}", "audible",
                   (p.get("product_images") or {}).get("500") or "")
        old = volumes.get(index)
        if old is None or (v.release and (not old.release or v.release < old.release)):
            volumes[index] = v

    def _find_audible_series(self, s: Series) -> str | None:
        want = _series_key_loose(s.name)
        asins = [o.asin for o in s.owned if o.asin][:20]
        products = []
        if asins:
            products = (self.audible.get_json(self.api, {"asins": ",".join(asins), "response_groups": "series"})
                        .get("products") or [])
        if not any(p.get("series") for p in products):
            products = (self.audible.get_json(self.api, {
                "title": s.name, "author": s.author, "num_results": 10, "products_sort_by": "Relevance",
                "response_groups": "series"}).get("products") or [])
        best = None
        for p in products:
            for x in p.get("series") or []:
                name = _series_key_loose(x.get("title") or "")
                if name == want:
                    return x.get("asin")
                # Audible's 'The World of Otome Games is Tough for Mobs' = your 'Trapped in a Dating Sim: The
                # World of Otome Games is Tough for Mobs' (the shared head left out)
                if ":" in s.name and _norm(x.get("title") or "") == _norm(s.name.split(":", 1)[1]):
                    return x.get("asin")
                # 'Overlord' ~ Audible's 'Overlord (Light Novel)' - but not 'Mushoku Tensei ... Recollections'
                # (a spin-off) ~ the main series 'Mushoku Tensei'
                if best is None and name and name.startswith(want):
                    best = x.get("asin")
        return best

    # -- AniList -------------------------------------------------------------------------------
    def anilist_volumes(self, s: Series) -> None:
        query = """query ($q: String, $f: [MediaFormat]) { Page(perPage: 6) { media(search: $q, type: MANGA,
            format_in: $f) { id siteUrl volumes status title { romaji english } synonyms } } }"""
        fmt = ["MANGA", "ONE_SHOT"] if s.kind == "manga" else ["NOVEL"]
        body = {"query": query, "variables": {"q": s.name, "f": fmt}}
        try:
            data = self.anilist.post_json("https://graphql.anilist.co", body)
        except OSError as e:
            if "429" not in str(e):
                raise
            import time  # noqa: PLC0415
            time.sleep(60)  # 'too many requests': AniList's minute is over after that
            data = self.anilist.post_json("https://graphql.anilist.co", body)
        want = _series_key_loose(s.name)
        for m in ((data.get("data") or {}).get("Page") or {}).get("media") or []:
            names = [m["title"].get("english") or "", m["title"].get("romaji") or "", *(m.get("synonyms") or [])]
            if any(_series_key_loose(n) == want for n in names if n):
                s.total_hint = m.get("volumes") or None
                s.status = m.get("status") or ""
                s.links["AniList"] = m.get("siteUrl") or ""
                return

    def anilist_manga(self, s: Series) -> None:
        """The manga of a light novel series: how many volumes (for its tab - shown, not counted as missing)."""
        query = """query ($q: String) { Page(perPage: 6) { media(search: $q, type: MANGA, format_in: [MANGA]) {
            id siteUrl volumes status title { romaji english } synonyms } } }"""
        try:
            data = self.anilist.post_json("https://graphql.anilist.co", {"query": query, "variables": {"q": s.name}})
        except OSError as e:
            if "429" in str(e):
                return  # 'too many requests': the next check asks again
            raise
        want = _series_key_loose(s.name)
        for m in ((data.get("data") or {}).get("Page") or {}).get("media") or []:
            names = [m["title"].get("english") or "", m["title"].get("romaji") or "", *(m.get("synonyms") or [])]
            if any(_series_key_loose(n) == want for n in names if n):
                s.manga_hint = m.get("volumes") or 0
                s.links["AniList manga"] = m.get("siteUrl") or ""
                return
        s.manga_hint = None

    # -- Google Books --------------------------------------------------------------------------
    def google_volumes(self, s: Series) -> None:
        q = f'intitle:"{s.name}"' + (f' inauthor:"{s.author}"' if s.author else "")
        params = {"q": q, "maxResults": 40, "printType": "books", "langRestrict": "en"}
        if self.settings.google_books_key:
            params["key"] = self.settings.google_books_key
        data = self.google.get_json("https://www.googleapis.com/books/v1/volumes", params)
        want = _series_key_loose(s.name)
        found: dict[float, Volume] = {}
        for it in data.get("items") or []:
            info = it.get("volumeInfo") or {}
            title = " ".join(x for x in (info.get("title"), info.get("subtitle")) if x)
            if not _series_key_loose(title).startswith(want):
                continue
            m = _VOL_RE.search(title[len(s.name):] if title.lower().startswith(s.name.lower()) else title)
            if not m:
                continue
            index = float(m.group(1) or m.group(2))
            if 0 < index < 200:
                found.setdefault(index, Volume(index, info.get("title") or "", (info.get("publishedDate") or "")[:10],
                                               url=info.get("infoLink") or "", source="google"))
        s.others.update({k: v for k, v in found.items() if k not in s.others})


def check_all(lookup: Lookup, series: list[Series], on_progress: Callable[[int, int, Series], None] | None = None,
              cancelled: Callable[[], bool] = lambda: False) -> None:
    for i, s in enumerate(series, 1):
        if cancelled():
            break
        lookup.check(s)
        if on_progress:
            on_progress(i, len(series), s)
