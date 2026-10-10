"""The window: filters on the left, your series in the middle, the selected series' volumes on the right."""
from __future__ import annotations

import datetime as dt
import webbrowser
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QIcon, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QGridLayout, QScrollArea, QStackedWidget,
                               QTabWidget, QToolButton, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QCheckBox, QComboBox, QTabBar)

from .. import __version__
from ..config import STATE, Settings, load_settings, save_settings
from ..core import store
from ..core.collect import collect, group
from ..core.lookup import TLDS, Lookup, check_all
from ..core.models import AUDIO, EBOOK, Series, fmt_index
from ..portable import ui_settings
from . import theme

FILTERS = [("All series", "all"), ("Missing books", "missing"), ("   🎧 Missing audiobooks", "missing_audio"),
           ("   📖 Missing ebooks", "missing_ebook"), ("Coming soon", "upcoming"),
           ("Complete", "complete"), ("Not checked online", "unchecked"), ("📅 Release calendar", "calendar"),
           ("📚 Standalone books", "standalone")]
ALONE_COLS = ["Title", "Author", "Kind", "Format", "Where", "Read"]
CALENDAR_COLS = ["Date", "Series", "#", "Title", "Author", "You have"]
KIND_FILTERS = [("Audiobooks", "audiobook"), ("Light novels", "light novel"), ("EBooks", "ebook"),
                ("Manga", "manga")]
SERIES_COLS = ["Series", "Author", "🎧", "📖 LN", "🗯 M", "📘 E", "Missing", "Next release"]
SERIES_TIPS = {2: "Audiobooks you have", 3: "Light novels you have (ebooks)", 4: "Manga you have",
               5: "Ebooks you have (not light novels)"}
COL_MISSING, COL_NEXT = 6, 7
VOLUME_COLS = ["#", "Title", "Have", "Release"]  # 'Have' is 🎧 or 📖 - the tab's format


class _Signals(QObject):
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)


class Worker(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn, self.signals, self.stop = fn, _Signals(), False

    def run(self) -> None:
        try:
            self.signals.done.emit(self.fn(self))
        except Exception as e:  # noqa: BLE001 - shown to the user
            self.signals.failed.emit(f"{type(e).__name__}: {e}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings: Settings = load_settings()
        self.series, self.alone, self.scanned = store.load(STATE)
        self.worker: Worker | None = None
        self.setWindowTitle(f"Book Collection Checker v{__version__}")
        self.resize(1500, 860)

        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._sidebar())
        main = QWidget()
        v = QVBoxLayout(main)
        v.setContentsMargins(12, 10, 12, 8)
        v.addLayout(self._toolbar())
        split = QSplitter()
        split.addWidget(self._series_table())
        split.addWidget(self._details())
        split.setSizes([1060, 480])
        self.pages = QStackedWidget()
        self.pages.addWidget(split)
        self.pages.addWidget(self._calendar())
        self.pages.addWidget(self._standalone())
        v.addWidget(self.pages, 1)
        h.addWidget(main, 1)
        self.setCentralWidget(root)
        self.status = QLabel()
        self.bar = QProgressBar()
        self.bar.setMaximumWidth(260)
        self.bar.setVisible(False)
        self.statusBar().addWidget(self.status, 1)
        self.statusBar().addPermanentWidget(self.bar)
        geo = ui_settings().value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        for table in (self.table, self.volumes, self.cal, self.alone_table):
            make_copyable(table)
        self._restore_sort()
        self.fill()
        QTimer.singleShot(0, self.scan)  # every start: what you own now (the online answers are kept)

    def _restore_sort(self) -> None:
        """The series list sorts like you left it (column + direction), saved on every header click."""
        header = self.table.horizontalHeader()
        try:
            col = int(ui_settings().value("series_sort_column", 0))
            order = Qt.SortOrder(int(ui_settings().value("series_sort_order", int(Qt.AscendingOrder.value))))
        except (TypeError, ValueError):
            col, order = 0, Qt.AscendingOrder
        if 0 <= col < self.table.columnCount():
            self.table.sortByColumn(col, order)
        header.sortIndicatorChanged.connect(
            lambda c, o: (ui_settings().setValue("series_sort_column", c),
                          ui_settings().setValue("series_sort_order", int(o.value))))

    # -- layout ------------------------------------------------------------------------------------
    def _sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(220)
        outer = QVBoxLayout(side)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        brand = QWidget()
        brand.setObjectName("brand")
        b = QVBoxLayout(brand)
        b.setContentsMargins(16, 10, 16, 8)
        b.setSpacing(0)
        title = QLabel("BOOK COLLECTION")
        title.setObjectName("brandtitle")
        sub = QLabel(f"have · missing · coming  ·  v{__version__}")
        sub.setObjectName("brandsub")
        b.addWidget(title)
        b.addWidget(sub)
        outer.addWidget(brand)
        body = QWidget()
        body.setObjectName("sidebody")
        outer.addWidget(body, 1)
        v = QVBoxLayout(body)
        v.setContentsMargins(10, 10, 10, 10)
        self.menu = QListWidget()
        self.menu.setObjectName("sidemenu")
        for label, key in FILTERS:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, ("show", key))
            self.menu.addItem(it)
        head = QListWidgetItem("KIND")
        head.setFlags(Qt.NoItemFlags)
        self.menu.addItem(head)
        for label, key in KIND_FILTERS:
            it = QListWidgetItem("   " + label)
            it.setData(Qt.UserRole, ("kind", key))
            self.menu.addItem(it)
        self.menu.setCurrentRow(0)
        self.menu.currentItemChanged.connect(lambda *_: self.fill())
        v.addWidget(self.menu, 1)
        for text, fn, primary in (("🔄  Scan library", self.scan, False), ("🌐  Check online", self.check_online, True),
                                  ("📋  Copy missing list", self.copy_missing, False),
                                  ("⚙  Settings", self.open_settings, False)):
            b = QPushButton(text)
            b.setObjectName("primary" if primary else "sideaction")
            b.setToolTip({"🔄  Scan library": "Read your libraries again (keeps what was found online)",
                          "🌐  Check online": "Look up the series shown for missing and upcoming books",
                          "📋  Copy missing list": "Missing and upcoming books of the series shown, to the clipboard",
                          }.get(text, ""))
            b.clicked.connect(lambda _=False, f=fn: f())
            v.addWidget(b)
        self.totals = QLabel()
        self.totals.setObjectName("muted")
        self.totals.setWordWrap(True)
        v.addWidget(self.totals)
        return side

    def _toolbar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search series or author…")
        self.search.textChanged.connect(lambda *_: self.fill())
        row.addWidget(self.search, 1)
        self.b_stop = QPushButton("Stop")
        self.b_stop.setVisible(False)
        self.b_stop.clicked.connect(lambda: self.worker and setattr(self.worker, "stop", True))
        row.addWidget(self.b_stop)
        return row

    def _series_table(self) -> QTableWidget:
        t = self.table = QTableWidget(0, len(SERIES_COLS))
        t.setHorizontalHeaderLabels(SERIES_COLS)
        for c, tip in SERIES_TIPS.items():
            t.horizontalHeaderItem(c).setToolTip(tip)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSortingEnabled(True)
        t.verticalHeader().setVisible(False)
        head = t.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.Interactive)
        head.setSectionResizeMode(0, QHeaderView.Stretch)  # the series name gets the room
        for c, width in enumerate((0, 150, 50, 72, 64, 60, 110, 140)):
            if width:
                t.setColumnWidth(c, width)
        t.setWordWrap(False)
        t.itemSelectionChanged.connect(self.show_series)
        return t

    def _calendar(self) -> QTableWidget:
        t = self.cal = QTableWidget(0, len(CALENDAR_COLS))
        t.setHorizontalHeaderLabels(CALENDAR_COLS)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.verticalHeader().setVisible(False)
        t.setWordWrap(False)
        head = t.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.Interactive)
        head.setSectionResizeMode(3, QHeaderView.Stretch)
        for c, width in ((0, 110), (1, 380), (2, 50), (4, 170), (5, 90)):
            t.setColumnWidth(c, width)
        t.itemDoubleClicked.connect(lambda it: (u := self.cal.item(it.row(), 0).data(Qt.UserRole)) and webbrowser.open(u))
        return t

    def _standalone(self) -> QTableWidget:
        t = self.alone_table = QTableWidget(0, len(ALONE_COLS))
        t.setHorizontalHeaderLabels(ALONE_COLS)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSortingEnabled(True)
        t.verticalHeader().setVisible(False)
        t.setWordWrap(False)
        head = t.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.Interactive)
        head.setSectionResizeMode(0, QHeaderView.Stretch)
        for c, width in ((1, 200), (2, 110), (3, 80), (4, 200), (5, 70)):
            t.setColumnWidth(c, width)
        t.itemDoubleClicked.connect(self._open_alone)
        return t

    def fill_standalone(self) -> None:
        q = self.search.text().strip().lower()
        rows = [o for o in self.alone if not q or q in o.title.lower() or q in o.author.lower()]
        t = self.alone_table
        t.setSortingEnabled(False)
        t.setRowCount(len(rows))
        for r, o in enumerate(sorted(rows, key=lambda o: (o.author.lower(), o.title.lower()))):
            read = "✔" if o.finished else (f"{o.progress:.0%}" if o.progress else "")
            for c, val in enumerate((o.title, o.author, o.kind, "🎧" if o.fmt == AUDIO else "📖", o.source, read)):
                it = QTableWidgetItem(val)
                if c == 0:
                    it.setData(Qt.UserRole, o.path)
                t.setItem(r, c, it)
        t.setSortingEnabled(True)

    def _open_alone(self, item: QTableWidgetItem) -> None:
        path = self.alone_table.item(item.row(), 0).data(Qt.UserRole)
        if path:
            p = Path(path)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(p if p.is_dir() else p.parent)))

    def fill_calendar(self) -> None:
        q = self.search.text().strip().lower()
        rows = []
        for s in self.series.values():
            if q and q not in s.name.lower() and q not in s.author.lower():
                continue
            for v in s.upcoming():
                rows.append((v.release, s, v))
        seen = set()  # the same volume from two series entries: once
        rows = [r for r in rows if not ((r[0], r[1].author, r[2].index, r[2].title) in seen
                                        or seen.add((r[0], r[1].author, r[2].index, r[2].title)))]
        rows.sort(key=lambda r: (r[0], r[1].name.lower()))
        t = self.cal
        t.setRowCount(len(rows))
        p = theme._current  # noqa: SLF001
        today = dt.date.today()
        for r, (date, s, v) in enumerate(rows):
            have = f"{len(s.owned)} books"
            for c, val in enumerate((date, s.name, fmt_index(v.index), v.title, s.author, have)):
                it = QTableWidgetItem(val)
                if c == 0:
                    it.setData(Qt.UserRole, v.url)
                    try:
                        soon = (dt.date.fromisoformat(date) - today).days <= 30
                    except ValueError:
                        soon = False
                    it.setForeground(QColor(p.check if soon else p.text))
                t.setItem(r, c, it)
        self.status.setText(f"{len(rows)} upcoming volumes - double-click one to open it on Audible")

    def _details(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 0, 0, 0)
        self.heading = QLabel("Select a series")
        self.heading.setObjectName("heading")
        self.heading.setWordWrap(True)
        v.addWidget(self.heading)
        top = QHBoxLayout()
        self.cover = QLabel()
        self.cover.setObjectName("cover")
        self.cover.setFixedSize(150, 150)
        self.cover.setAlignment(Qt.AlignCenter)
        top.addWidget(self.cover)
        right = QVBoxLayout()
        self.info = QLabel()
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText)
        self.info.setOpenExternalLinks(True)
        right.addWidget(self.info)
        right.addWidget(QLabel("Count missing for:"))
        self.track_audio = QCheckBox("🎧 Audiobooks")
        self.track_ebook = QCheckBox("📖 Ebooks")
        for cb in (self.track_audio, self.track_ebook):
            cb.setToolTip("Which formats you collect this series in - missing books are only counted for these")
            cb.toggled.connect(self._track_changed)
            right.addWidget(cb)
        right.addStretch()
        top.addLayout(right, 1)
        v.addLayout(top)
        self.net = QNetworkAccessManager(self)
        self.net.finished.connect(self._cover_loaded)
        self._cover_for = ""
        self.fmt_tabs = QTabBar()
        self.fmt_tabs.setExpanding(False)
        self.fmt_tabs.setDrawBase(False)
        self.fmt_tabs.currentChanged.connect(self._show_tab)
        v.addWidget(self.fmt_tabs)
        self._tabs: list[tuple[Series, str]] = []  # (series, format) per tab
        self._shown: Series | None = None           # the series of the open tab
        t = self.volumes = QTableWidget(0, len(VOLUME_COLS))
        t.setHorizontalHeaderLabels(VOLUME_COLS)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.verticalHeader().setVisible(False)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.setWordWrap(False)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for c in (0, 2, 3):
            t.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        t.itemDoubleClicked.connect(self._open_volume)
        t.itemSelectionChanged.connect(self._volume_cover)
        v.addWidget(t, 1)
        row = QHBoxLayout()
        b = QPushButton("Check this series again")
        b.clicked.connect(self.check_selected)
        row.addWidget(b)
        b = QPushButton("Hide this series")
        b.clicked.connect(self.hide_selected)
        row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        return w

    # -- showing -------------------------------------------------------------------------------------
    def _visible(self) -> list[Series]:
        item = self.menu.currentItem()
        what, key = item.data(Qt.UserRole) if item and item.data(Qt.UserRole) else ("show", "all")
        q = self.search.text().strip().lower()
        out = []
        for s in self.series.values():
            if q and q not in s.name.lower() and q not in s.author.lower():
                continue
            if what == "kind" and s.kind != key:
                continue
            if key == "missing" and not s.missing_count:
                continue
            if key == "missing_audio" and not s.missing(AUDIO):
                continue
            if key == "missing_ebook" and not s.missing(EBOOK):
                continue
            if key == "upcoming" and not s.upcoming():
                continue
            if key == "complete" and (s.missing_count or not s.checked):
                continue
            if key == "unchecked" and s.checked:
                continue
            out.append(s)
        return out

    def fill(self) -> None:
        item = self.menu.currentItem()
        calendar = bool(item and item.data(Qt.UserRole) == ("show", "calendar"))
        alone = bool(item and item.data(Qt.UserRole) == ("show", "standalone"))
        self.pages.setCurrentIndex(1 if calendar else 2 if alone else 0)
        if alone:
            self.fill_standalone()
            self._totals()
            self.status.setText(f"{self.alone_table.rowCount()} books without a series - double-click to open the folder")
            return
        if calendar:
            self.fill_calendar()
            self._totals()
            self.status.setText(f"{self.cal.rowCount()} upcoming volumes - double-click one to open it on Audible")
            return
        keep = self._selected_key()
        t = self.table
        t.setSortingEnabled(False)
        rows = self._visible()
        t.setRowCount(len(rows))
        p = theme._current  # noqa: SLF001 - the theme's colours
        for r, s in enumerate(rows):
            nxt = s.next_release
            miss = s.missing_count
            book = _book_type(s)
            values = [s.name, s.author, _have(s, AUDIO), *(_have(s, EBOOK) if book == b else "" for b in ("LN", "M", "E")),
                      (_missing_text(s) if miss else ("✓" if s.checked else "")),
                      (f"#{fmt_index(nxt.index)}  {nxt.release}" if nxt else "")]
            for c, val in enumerate(values):
                it = _Item(val)
                if c == 0:
                    it.setData(Qt.UserRole, s.key)
                    it.setToolTip(f"{s.name}\n{s.author} · {len(s.owned)} books")
                if c == COL_MISSING:
                    it.setData(Qt.UserRole + 1, miss)  # sorts by the total
                    it.setForeground(QColor(p.problem if miss else p.ready))
                    if miss:
                        a, e = len(s.missing(AUDIO)), len(s.missing(EBOOK))
                        word = {"LN": "light novel(s)", "M": "manga", "E": "ebook(s)"}[book]
                        it.setToolTip("\n".join(x for x in (f"{a} audiobook(s) missing" if a else "",
                                                            f"{e} {word} missing" if e else "") if x))
                if c == COL_NEXT and nxt:
                    it.setForeground(QColor(p.check))
                t.setItem(r, c, it)
        t.setSortingEnabled(True)
        self._select(keep)
        self._totals()

    def _totals(self) -> None:
        all_ = list(self.series.values())
        missing = sum(s.missing_count for s in all_)
        ups = sum(len(s.upcoming()) for s in all_)
        books = sum(len(s.owned) for s in all_) + len(self.alone)
        checked = sum(1 for s in all_ if s.checked)
        self.totals.setText(f"{books} books, {len(all_)} series\n{checked} checked online\n"
                            f"{missing} missing · {ups} coming soon\nScanned {self.scanned[:16].replace('T', ' ')}")
        self.status.setText(f"{self.table.rowCount()} series shown")

    def _selected_key(self) -> str | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.table.item(rows[0].row(), 0).data(Qt.UserRole) if rows else None

    def _select(self, key: str | None) -> None:
        if key is None:
            return
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).data(Qt.UserRole) == key:
                self.table.selectRow(r)
                return

    def _family(self, s: Series) -> list[tuple[str, Series, str]]:
        """The tabs for a series: its audiobooks, its light novels / ebooks, and its manga (the same series under
        another key) - only the ones it has, owned or known online."""
        base = s.key.split("|")[0]
        novel, manga = self.series.get(base), self.series.get(base + "|manga")
        tabs = []
        if novel is not None:  # what exists - also the formats you don't have (shown, counted only if you collect them)
            if any(o.fmt == AUDIO for o in novel.owned) or novel.audible:
                tabs.append(("🎧 Audiobook", novel, AUDIO))
            if any(o.fmt == EBOOK for o in novel.owned) or novel.known(EBOOK):
                tabs.append(("📖 Light novel" if novel.kind == "light novel" else "📖 Ebook", novel, EBOOK))
        if manga is None and novel is not None and novel.manga_hint is not None:
            # a manga you don't have: AniList's volumes, nothing counted as missing
            manga = Series(base + "|manga", f"{novel.name} (Manga)", novel.author, "manga",
                           total_hint=novel.manga_hint or None, checked=novel.checked,
                           links={k: v for k, v in novel.links.items() if k == "AniList manga"})
        if manga is not None:
            tabs.append(("🗯 Manga", manga, EBOOK))
        return tabs or [("📖 Ebook", s, EBOOK)]

    def show_series(self) -> None:
        key = self._selected_key()
        s = self.series.get(key) if key else None
        if s is None:
            self._shown, self._tabs = None, []
            self.heading.setText("Select a series")
            self.info.clear()
            self.cover.clear()
            self.volumes.setRowCount(0)
            self.fmt_tabs.blockSignals(True)
            while self.fmt_tabs.count():
                self.fmt_tabs.removeTab(0)
            self.fmt_tabs.blockSignals(False)
            return
        family = self._family(s)
        cur = self.fmt_tabs.currentIndex()
        keep = (self._tabs[cur][0].key, self._tabs[cur][1]) if 0 <= cur < len(self._tabs) else None
        self.fmt_tabs.blockSignals(True)
        while self.fmt_tabs.count():
            self.fmt_tabs.removeTab(0)
        self._tabs = []
        for label, ser, fmt in family:
            self.fmt_tabs.addTab(label)
            self._tabs.append((ser, fmt))
        # the same tab again (after a refresh), else the clicked row's own series: manga row -> the manga tab
        pick = next((i for i, (ser, fmt) in enumerate(self._tabs) if keep and (ser.key, fmt) == keep
                     and ser.key.split("|")[0] == s.key.split("|")[0]), None)
        if pick is None:
            pick = next((i for i, (ser, _) in enumerate(self._tabs) if ser.key == s.key), 0)
        self.fmt_tabs.setCurrentIndex(pick)
        self.fmt_tabs.setVisible(len(self._tabs) > 1)
        self.fmt_tabs.blockSignals(False)
        self._show_tab(pick)

    def _show_tab(self, index: int) -> None:
        if not (0 <= index < len(self._tabs)):
            return
        s, fmt = self._tabs[index]
        self._shown = s
        t = self.volumes
        self.heading.setText(f"{s.name} — {s.author}")
        accent = theme._current.accent  # noqa: SLF001
        links = " · ".join(f'<a href="{u}" style="color:{accent}">{n}</a>' for n, u in s.links.items()
                           if n != "errors" and u)
        extra = []
        if s.total_hint and fmt == EBOOK:
            extra.append(f"AniList: {s.total_hint} volumes ({s.status.lower()})")
            more = s.anilist_unlisted(EBOOK)
            if more:
                extra.append(f"{more} more not listed here (AniList counts side volumes like .5 too)")
        if s.key not in self.series:  # a manga of this series you don't have
            extra.append(f"you don't have it - AniList: {s.total_hint} volumes" if s.total_hint
                         else "you don't have it - still running on AniList, no volume count yet")
        elif not mine_any(s, fmt) and fmt not in s.formats:
            extra.append("you don't have these - shown, not counted (tick it below to collect it)")
        if s.links.get("errors"):
            extra.append(f"⚠ {s.links['errors']}")
        if not s.checked:
            extra.append("Not checked online yet - click Check online")
        else:
            extra.append(f"checked {s.checked.replace('T', ' ')}")
        self.info.setText(" · ".join(x for x in [s.kind, links, *extra] if x))
        for cb, f in ((self.track_audio, AUDIO), (self.track_ebook, EBOOK)):
            cb.blockSignals(True)
            cb.setChecked(f in s.formats)
            cb.setEnabled(s.key in self.series)  # a manga you don't have isn't in your collection to set
            cb.blockSignals(False)
        mine = [o for o in s.owned if o.fmt == fmt]
        first = min(mine or s.owned, key=lambda o: o.index if o.index is not None else 9999, default=None)
        self._show_cover(first, next(iter(sorted(s.audible.items())), (None, None))[1])
        have: dict[float, object] = {}
        for o in mine:
            if (i := s.index_of(o)) is not None:
                have.setdefault(i, o)
        known = s.known(fmt)
        indexes = sorted(set(known) | set(have))
        t.setHorizontalHeaderLabels(["#", "Title", "🎧" if fmt == AUDIO else "📖", "Release"])
        p = theme._current  # noqa: SLF001
        t.setRowCount(len(indexes))
        for r, i in enumerate(indexes):
            vol = known.get(i) or s.audible.get(i) or s.others.get(i)
            owned = have.get(i)
            title = (owned.title if owned else "") or (vol.title if vol else "")
            if owned is not None:
                cell = "✓" + (" ✔" if owned.finished else "")
            elif fmt in s.formats and i in known and not (vol and vol.upcoming):
                cell = "missing"
            else:
                cell = ""
            where = owned.source if owned else ""
            upcoming = bool(vol and vol.upcoming and not owned)
            row = [fmt_index(i), title, cell, vol.release if vol else ""]
            for c, val in enumerate(row):
                it = QTableWidgetItem(val)
                it.setToolTip(f"{title}\n{where}" if where else title)
                if c == 0:
                    it.setData(Qt.UserRole, (vol.url if vol else "") or (owned.path if owned else ""))
                if cell == "missing":
                    it.setForeground(QColor(p.problem))
                elif upcoming:
                    it.setForeground(QColor(p.check))
                t.setItem(r, c, it)

    # -- covers ----------------------------------------------------------------------------------------
    def _volume_cover(self) -> None:
        s = self._shown
        rows = self.volumes.selectionModel().selectedRows()
        if s is None or not rows:
            return
        try:
            i = float(self.volumes.item(rows[0].row(), 0).text())
        except ValueError:
            return
        owned = next((o for o in s.owned if s.index_of(o) == i), None)
        self._show_cover(owned, s.audible.get(i))

    def _show_cover(self, owned, vol) -> None:
        """From your own copy (Audiobookshelf, or a cover file in its folder), else Audible's picture."""
        self.cover.setText("no cover")
        if owned is not None and owned.path:
            folder = Path(owned.path)
            folder = folder if folder.is_dir() else folder.parent
            for name in ("cover.jpg", "cover.png", "cover.jpeg", "folder.jpg"):
                if (folder / name).is_file():
                    self._set_cover(QPixmap(str(folder / name)))
                    return
        url, headers = "", {}
        if owned is not None and owned.item_id and self.settings.abs_url and self.settings.abs_api_key:
            url = f"{self.settings.abs_url}/api/items/{owned.item_id}/cover?width=300"
            headers = {"Authorization": f"Bearer {self.settings.abs_api_key}"}
        elif vol is not None and vol.cover:
            url = vol.cover
        if not url:
            return
        self._cover_for = url
        req = QNetworkRequest(QUrl(url))
        for k, val in headers.items():
            req.setRawHeader(k.encode(), val.encode())
        self.net.get(req)

    def _cover_loaded(self, reply: QNetworkReply) -> None:
        reply.deleteLater()
        if reply.url().toString() != QUrl(self._cover_for).toString() or reply.error() != QNetworkReply.NoError:
            return
        pix = QPixmap()
        if pix.loadFromData(reply.readAll()):
            self._set_cover(pix)

    def _set_cover(self, pix: QPixmap) -> None:
        if not pix.isNull():
            self.cover.setPixmap(pix.scaled(150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _track_changed(self) -> None:
        s = self._shown
        if s is None:
            return
        chosen = [f for cb, f in ((self.track_audio, AUDIO), (self.track_ebook, EBOOK)) if cb.isChecked()]
        s.track = [] if set(chosen) == {o.fmt for o in s.owned} else chosen
        store.save(STATE, self.series, self.alone, self.scanned)
        self.fill()
        self.show_series()

    def _open_volume(self, item: QTableWidgetItem) -> None:
        target = self.volumes.item(item.row(), 0).data(Qt.UserRole)
        if not target:
            return
        if target.startswith("http"):
            webbrowser.open(target)
        else:
            path = Path(target)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path if path.is_dir() else path.parent)))

    # -- actions -------------------------------------------------------------------------------------
    def _run(self, fn, done, label: str) -> None:
        if self.worker is not None:
            return
        self.worker = Worker(fn)
        self.worker.signals.progress.connect(self.status.setText)
        self.worker.signals.done.connect(lambda r: (self._idle(), done(r)))
        self.worker.signals.failed.connect(lambda e: (self._idle(), QMessageBox.warning(self, "Book Collection Checker", e)))
        self.bar.setRange(0, 0)
        self.bar.setVisible(True)
        self.b_stop.setVisible(True)
        self.status.setText(label)
        QThreadPool.globalInstance().start(self.worker)

    def _idle(self) -> None:
        self.worker = None
        self.bar.setVisible(False)
        self.b_stop.setVisible(False)

    def scan(self) -> None:
        settings = self.settings

        def work(w: Worker):
            owned = collect(settings, w.signals.progress.emit)
            return group(owned, set(settings.hidden_series))

        def done(result):
            series, alone = result
            store.merge_lookups(series, self.series)
            self.series, self.alone = series, alone
            self.scanned = dt.datetime.now().isoformat(timespec="minutes")
            store.save(STATE, self.series, self.alone, self.scanned)
            self.fill()
            if not any(s.checked for s in self.series.values()):
                if QMessageBox.question(self, "Book Collection Checker",
                                        f"Found {len(series)} series. Check them online now for missing and "
                                        "upcoming books? (about 2 s per series the first time)") == QMessageBox.Yes:
                    self.check_online()

        self._run(work, done, "Reading your libraries…")

    def check_online(self, only: list[Series] | None = None) -> None:
        todo = only if only is not None else self._visible()
        if not todo:
            return
        lookup = Lookup(self.settings, STATE)

        def work(w: Worker):
            check_all(lookup, todo, lambda i, n, s: w.signals.progress.emit(f"Checking {i}/{n}: {s.name}"),
                      cancelled=lambda: w.stop)
            return len(todo)

        def done(_):
            store.save(STATE, self.series, self.alone, self.scanned)
            self.fill()
            self.show_series()

        self._run(work, done, f"Checking {len(todo)} series online…")

    def check_selected(self) -> None:
        if self._shown is not None:
            self.check_online([self._shown])

    def hide_selected(self) -> None:
        key = self._selected_key()
        if not key:
            return
        self.settings.hidden_series.append(key)
        save_settings(self.settings)
        self.series.pop(key, None)
        store.save(STATE, self.series, self.alone, self.scanned)
        self.fill()

    def copy_missing(self) -> None:
        lines = []
        for s in sorted(self._visible(), key=lambda s: s.name.lower()):
            for f, icon in ((AUDIO, "audiobook"), (EBOOK, "ebook")):
                miss = s.missing(f)
                if miss:
                    nums = ", ".join(fmt_index(v.index) for v in miss)
                    lines.append(f"{s.name} ({s.author}) - {icon}: {nums}")
            for v in s.upcoming():
                lines.append(f"{s.name} ({s.author}) - coming {v.release}: #{fmt_index(v.index)} {v.title}")
        if not lines:
            QMessageBox.information(self, "Missing", "Nothing missing in the series shown.")
            return
        QGuiApplication.clipboard().setText("\n".join(lines))
        QMessageBox.information(self, "Missing", f"Copied {len(lines)} lines to the clipboard.")

    def open_settings(self) -> None:
        before = self.settings.theme
        dlg = SettingsDialog(self.settings, self)
        dlg.preview.connect(self._preview_theme)
        if dlg.exec():
            save_settings(self.settings)
            self._preview_theme(self.settings.theme)
            if dlg.sources_changed:
                self.scan()
        else:
            self._preview_theme(before)  # Cancel: the old colours back

    def _preview_theme(self, name: str) -> None:
        theme.apply(name)
        self.fill()  # the table colours come from the theme
        self.show_series()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt
        ui_settings().setValue("geometry", self.saveGeometry())
        super().closeEvent(event)


def _have(s: Series, fmt: str) -> str:
    n = len(s.have(fmt))
    return str(n) if n else ""


def make_copyable(table: QTableWidget) -> None:
    """Ctrl+C copies the selected rows (tab between cells); right-click: Copy (the cell) / Copy row."""
    from PySide6.QtGui import QAction, QKeySequence  # noqa: PLC0415
    from PySide6.QtWidgets import QMenu  # noqa: PLC0415

    def rows_text() -> str:
        rows = sorted({i.row() for i in table.selectedIndexes()})
        return "\n".join("\t".join(table.item(r, c).text() if table.item(r, c) else ""
                                   for c in range(table.columnCount()) if not table.isColumnHidden(c))
                         for r in rows)

    copy = QAction("Copy", table)
    copy.setShortcut(QKeySequence.Copy)
    copy.setShortcutContext(Qt.WidgetShortcut)
    copy.triggered.connect(lambda: QGuiApplication.clipboard().setText(rows_text()))
    table.addAction(copy)

    def menu(pos) -> None:
        item = table.itemAt(pos)
        m = QMenu(table)
        if item is not None:
            m.addAction("Copy", lambda: QGuiApplication.clipboard().setText(item.text()))
        m.addAction("Copy row", lambda: QGuiApplication.clipboard().setText(rows_text()))
        m.exec(table.viewport().mapToGlobal(pos))

    table.setContextMenuPolicy(Qt.CustomContextMenu)
    table.customContextMenuRequested.connect(menu)


def _book_type(s: Series) -> str:
    """The books of a series as a column: LN (light novel), M (manga / comic) or E (other ebooks)."""
    return "M" if s.kind in ("manga", "comic") or s.key.endswith("|manga") else "LN" if s.kind == "light novel" else "E"


def _missing_text(s: Series) -> str:
    """'🎧 2  LN 4' - what is missing of which format."""
    label = {"LN": "📖 LN", "M": "🗯 M", "E": "📘 E"}[_book_type(s)]
    parts = [f"{icon} {n}" for icon, n in (("🎧", len(s.missing(AUDIO))), (label, len(s.missing(EBOOK)))) if n]
    return "  ".join(parts)


class _Item(QTableWidgetItem):
    """Sorts numbers as numbers ('10' after '9'); a cell with a sort value (Missing) by that."""

    def __lt__(self, other) -> bool:  # noqa: D105
        ka, kb = self.data(Qt.UserRole + 1), other.data(Qt.UserRole + 1)
        if ka is not None or kb is not None:
            return (ka or 0) < (kb or 0)
        a, b = self.text(), other.text()
        try:
            return float(a or -1) < float(b or -1)
        except ValueError:
            return a.lower() < b.lower()


class ThemePicker(QWidget):
    """Every theme as a clickable preview tile (like the Book Sorter's)."""
    picked = Signal(str)

    def __init__(self, current: str, columns: int = 4, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setSpacing(10)
        self._group = QButtonGroup(self)
        self._current = current
        for i, name in enumerate(theme.THEMES):
            tile = QToolButton()
            tile.setObjectName("themetile")
            tile.setCheckable(True)
            tile.setChecked(name == current)
            tile.setText(name)
            tile.setIcon(QIcon(theme.swatch(name)))
            tile.setIconSize(QSize(150, 84))
            tile.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            tile.setCursor(Qt.PointingHandCursor)
            tile.clicked.connect(lambda _=False, n=name: self._pick(n))
            self._group.addButton(tile)
            grid.addWidget(tile, i // columns, i % columns)

    def _pick(self, name: str) -> None:
        self._current = name
        self.picked.emit(name)

    def currentText(self) -> str:
        return self._current


class SettingsDialog(QDialog):
    preview = Signal(str)

    def __init__(self, s: Settings, parent=None):
        super().__init__(parent)
        self.s = s
        self.sources_changed = False
        self._hidden_before = list(s.hidden_series)
        self.setWindowTitle("Settings")
        self.resize(780, 620)
        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs, 1)
        page = QWidget()
        form = QFormLayout(page)
        tabs.addTab(page, "Your books")
        self.use_abs = QCheckBox("Read my Audiobookshelf libraries (and listening progress)")
        self.use_abs.setChecked(s.use_abs)
        form.addRow(self.use_abs)
        self.abs_url = QLineEdit(s.abs_url)
        self.abs_url.setPlaceholderText("http://localhost:13378/audiobookshelf")
        form.addRow("ABS address", self.abs_url)
        self.abs_key = QLineEdit(s.abs_api_key)
        self.abs_key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        form.addRow("ABS API key", self.abs_key)
        self.libs = QPlainTextEdit("\n".join(s.library_folders))
        self.libs.setPlaceholderText("Library folders, one per line (only read)")
        self.libs.setFixedHeight(90)
        form.addRow("Library folders", self._with_add(self.libs))
        self.staging = QPlainTextEdit("\n".join(s.staging_folders))
        self.staging.setPlaceholderText("e.g. the Book Sorter's sorted folders")
        self.staging.setFixedHeight(70)
        form.addRow("Staging folders", self._with_add(self.staging))
        page = QWidget()
        form = QFormLayout(page)
        tabs.addTab(page, "Look up")
        self.region = QComboBox()
        self.region.addItems(list(TLDS))
        self.region.setCurrentText(s.audible_region)
        form.addRow("Audible store", self.region)
        self.use_audible = QCheckBox("Audible - every volume of a series, with release dates")
        self.use_audible.setChecked(s.use_audible)
        self.use_anilist = QCheckBox("AniList - light novel / manga volume counts")
        self.use_anilist.setChecked(s.use_anilist)
        self.use_google = QCheckBox("Google Books - ebook volumes Audible doesn't sell")
        self.use_google.setChecked(s.use_google)
        self.google_key = QLineEdit(s.google_books_key)
        self.google_key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.google_key.setPlaceholderText("optional - without it Google often says 'too many requests'")
        form.addRow("Look up", self.use_audible)
        form.addRow("", self.use_anilist)
        form.addRow("", self.use_google)
        form.addRow("Google key", self.google_key)
        hidden = QLabel(f"{len(s.hidden_series)} hidden series")
        b = QPushButton("Show them again")
        b.clicked.connect(lambda: (s.hidden_series.clear(), hidden.setText("0 hidden series")))
        row = QHBoxLayout()
        row.addWidget(hidden)
        row.addWidget(b)
        row.addStretch()
        form.addRow("Hidden", row)
        look = QScrollArea()
        look.setWidgetResizable(True)
        look.setFrameShape(QFrame.NoFrame)
        box = QWidget()
        lv = QVBoxLayout(box)
        lv.addWidget(QLabel("Colour theme - click one to try it, Save to keep it"))
        self.theme = ThemePicker(s.theme if s.theme in theme.THEMES else theme.DEFAULT_THEME)
        self.theme.picked.connect(self.preview.emit)
        lv.addWidget(self.theme)
        lv.addStretch()
        look.setWidget(box)
        tabs.addTab(look, "Look")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _with_add(self, edit: QPlainTextEdit) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1)
        b = QPushButton("Add…")
        b.clicked.connect(lambda: (d := QFileDialog.getExistingDirectory(self, "Folder")) and edit.appendPlainText(d))
        h.addWidget(b, 0, Qt.AlignTop)
        return w

    def _save(self) -> None:
        lines = lambda e: [x.strip() for x in e.toPlainText().splitlines() if x.strip()]  # noqa: E731
        s = self.s
        before = (s.use_abs, s.abs_url, s.abs_api_key, list(s.library_folders), list(s.staging_folders),
                  self._hidden_before)
        s.use_abs, s.abs_url, s.abs_api_key = self.use_abs.isChecked(), self.abs_url.text().strip().rstrip("/"), \
            self.abs_key.text().strip()
        s.library_folders, s.staging_folders = lines(self.libs), lines(self.staging)
        s.audible_region = self.region.currentText()
        s.use_audible, s.use_anilist, s.use_google = (self.use_audible.isChecked(), self.use_anilist.isChecked(),
                                                      self.use_google.isChecked())
        s.google_books_key = self.google_key.text().strip()
        s.theme = self.theme.currentText()
        self.sources_changed = before != (s.use_abs, s.abs_url, s.abs_api_key, s.library_folders, s.staging_folders,
                                          s.hidden_series)
        self.accept()


def mine_any(s: Series, fmt: str) -> bool:
    """Do you have any volume of this series in this format?"""
    return any(o.fmt == fmt for o in s.owned)
