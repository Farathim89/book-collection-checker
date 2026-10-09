"""Settings: where your books are (Audiobookshelf, library folders, the sorter's staging folders) and which
online sources say what a series has. Saved as settings.json in the data folder."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .portable import PORTABLE, app_dir

STATE: Path = PORTABLE if PORTABLE is not None else \
    Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "BookCollectionChecker"
SETTINGS_FILE = STATE / "settings.json"


@dataclass
class Settings:
    use_abs: bool = True
    abs_url: str = ""            # e.g. http://localhost:13378/audiobookshelf
    abs_api_key: str = ""        # ABS: Settings > API Keys
    library_folders: list[str] = field(default_factory=list)   # read only - never written to
    staging_folders: list[str] = field(default_factory=list)   # e.g. the Book Sorter's sorted folders
    audible_region: str = "us"
    use_audible: bool = True     # every volume Audible sells in the series, with release dates
    use_anilist: bool = True     # light novel / manga volume counts
    use_google: bool = True      # ebook volumes (Google Books)
    google_books_key: str = ""   # optional; without it Google often answers 'too many requests'
    hidden_series: list[str] = field(default_factory=list)  # series keys you don't want to see
    theme: str = "Gold (dark)"


def load_settings(path: Path = SETTINGS_FILE) -> Settings:
    s = Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = import_from_book_sorter(s)
        try:
            save_settings(s, path)  # first run: keep what came from the Book Sorter
        except OSError:
            pass
        return s
    for f in fields(Settings):
        if f.name in data:
            setattr(s, f.name, data[f.name])
    return s


def save_settings(s: Settings, path: Path = SETTINGS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=1), encoding="utf-8")


def book_sorter_settings() -> Path | None:
    """The Book Sorter next door (D:\\Ai - Programs\\AudioBooks & EBook Sorter): its portable settings."""
    for p in (app_dir().parent / "AudioBooks & EBook Sorter" / "dist" / "BookSorter-data" / "settings.json",
              app_dir().parent.parent / "AudioBooks & EBook Sorter" / "dist" / "BookSorter-data" / "settings.json",
              Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "BookSorter" / "settings.json"):
        if p.is_file():
            return p
    return None


def import_from_book_sorter(s: Settings) -> Settings:
    """First run: take the ABS address/key, the library folders (the sorter's protected ones) and its
    sorted (staging) folders from the Book Sorter - nothing to type."""
    path = book_sorter_settings()
    if path is None:
        return s
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return s
    s.abs_url = data.get("abs_url") or s.abs_url
    s.abs_api_key = data.get("abs_api_key") or s.abs_api_key
    s.audible_region = data.get("audible_region") or s.audible_region
    s.google_books_key = data.get("google_books_key") or s.google_books_key
    s.library_folders = [p for p in data.get("protected") or [] if "podcast" not in p.lower()]
    s.staging_folders = [p for p in (data.get("audio_sorted"), data.get("books_sorted"),
                                     data.get("swedish_sorted")) if p]
    return s
