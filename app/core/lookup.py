"""What a series has, online:
    Audible  - every volume it sells (number, title, release date - also upcoming ones)
    AniList  - how many volumes a light novel / manga has, and if it is finished
    Google   - ebook volumes Audible doesn't sell (numbers found in the titles)
Answers are cached for a week (providers.http)."""
from __future__ import annotations

import datetime as dt
import re
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
        data = self.audible.get_json(f"{self.api}/{series_asin}", {"response_groups": "relationships,product_desc"})
        product = data.get("product") or {}
        kids = [r["asin"] for r in product.get("relationships") or []
                if r.get("relationship_to_product") == "child" and r.get("relationship_type") == "series"]
        volumes: dict[float, Volume] = {}
        for i in range(0, len(kids), 50):
            res = self.audible.get_json(self.api, {"asins": ",".join(kids[i:i + 50]),
                                                   "response_groups": "product_desc,series,product_attrs"})
            for p in res.get("products") or []:
                seq = next((x.get("sequence") for x in p.get("series") or [] if x.get("asin") == series_asin), None)
                try:
                    index = float(str(seq).split("-")[0])
                except (TypeError, ValueError):
                    continue
                release = (p.get("release_date") or "")[:10]
                if release.startswith(_PLACEHOLDER) or (p.get("language") or "english").lower() != "english" \
                        and self.settings.audible_region in ("us", "uk", "ca", "au", "in"):
                    continue
                v = Volume(index, p.get("title") or "", release, p.get("asin") or "",
                           f"{self.site}/pd/{p.get('asin')}", "audible")
                old = volumes.get(index)
                if old is None or (v.release and (not old.release or v.release < old.release)):
                    volumes[index] = v
        s.audible = volumes
        s.links["Audible"] = f"{self.site}/series/{series_asin}"

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
