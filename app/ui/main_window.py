"""The window: filters on the left, your series in the middle, the selected series' volumes on the right."""
from __future__ import annotations

import datetime as dt
import webbrowser
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QAbstractItemView, QStackedWidget, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QCheckBox, QComboBox)

from .. import __version__
from ..config import STATE, Settings, load_settings, save_settings
from ..core import store
from ..core.collect import collect, group
from ..core.lookup import TLDS, Lookup, check_all
from ..core.models import AUDIO, EBOOK, Series, fmt_index
from ..portable import ui_settings
from . import theme

FILTERS = [("All series", "all"), ("Missing books", "missing"), ("Coming soon", "upcoming"),
           ("Complete", "complete"), ("Not checked online", "unchecked"), ("📅 Release calendar", "calendar")]
CALENDAR_COLS = ["Date", "Series", "#", "Title", "Author", "You have"]
KIND_FILTERS = [("Audiobooks", "audiobook"), ("Light novels", "light novel"), ("EBooks", "ebook"),
                ("Manga", "manga")]
SERIES_COLS = ["Series", "Author", "Kind", "🎧", "📖", "Missing", "Next release"]
VOLUME_COLS = ["#", "Title", "🎧", "📖", "Release", "Where"]


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
        self.fill()
        if not self.series:
            self.scan()

    # -- layout ------------------------------------------------------------------------------------
    def _sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(220)
        v = QVBoxLayout(side)
        v.setContentsMargins(10, 14, 10, 10)
        title = QLabel("Book Collection\nChecker")
        title.setObjectName("pagetitle")
        v.addWidget(title)
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
        for text, fn, primary in (("Scan library", self.scan, False), ("Check online", self.check_online, True),
                                  ("Copy missing list", self.copy_missing, False), ("Settings", self.open_settings, False)):
            b = QPushButton(text)
            if primary:
                b.setObjectName("primary")
            b.clicked.connect(fn)
            row.addWidget(b)
        self.b_stop = QPushButton("Stop")
        self.b_stop.setVisible(False)
        self.b_stop.clicked.connect(lambda: self.worker and setattr(self.worker, "stop", True))
        row.addWidget(self.b_stop)
        return row

    def _series_table(self) -> QTableWidget:
        t = self.table = QTableWidget(0, len(SERIES_COLS))
        t.setHorizontalHeaderLabels(SERIES_COLS)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSortingEnabled(True)
        t.verticalHeader().setVisible(False)
        head = t.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.Interactive)
        head.setSectionResizeMode(0, QHeaderView.Stretch)  # the series name gets the room
        for c, width in enumerate((0, 150, 90, 46, 46, 70, 140)):
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
        t = self.volumes = QTableWidget(0, len(VOLUME_COLS))
        t.setHorizontalHeaderLabels(VOLUME_COLS)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for c in (0, 2, 3, 4, 5):
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
        self.pages.setCurrentIndex(1 if calendar else 0)
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
            values = [s.name, s.author, s.kind, _have(s, AUDIO), _have(s, EBOOK),
                      (str(miss) if miss else ("✓" if s.checked else "")),
                      (f"#{fmt_index(nxt.index)}  {nxt.release}" if nxt else "")]
            for c, val in enumerate(values):
                it = _Item(val)
                if c == 0:
                    it.setData(Qt.UserRole, s.key)
                    it.setToolTip(f"{s.name}\n{s.author} · {len(s.owned)} books")
                if c == 5:
                    it.setData(Qt.UserRole + 1, miss)
                    it.setForeground(QColor(p.problem if miss else p.ready))
                if c == 6 and nxt:
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

    def show_series(self) -> None:
        key = self._selected_key()
        s = self.series.get(key) if key else None
        t = self.volumes
        if s is None:
            self.heading.setText("Select a series")
            self.info.clear()
            self.cover.clear()
            t.setRowCount(0)
            return
        self.heading.setText(f"{s.name} — {s.author}")
        links = " · ".join(f'<a href="{u}">{n}</a>' for n, u in s.links.items() if n != "errors" and u)
        extra = []
        if s.total_hint:
            extra.append(f"AniList: {s.total_hint} volumes ({s.status.lower()})")
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
            cb.blockSignals(False)
        first = min((o for o in s.owned), key=lambda o: o.index if o.index is not None else 9999, default=None)
        self._show_cover(first, next(iter(sorted(s.audible.items())), (None, None))[1])
        have = {f: {} for f in (AUDIO, EBOOK)}
        for o in s.owned:
            if o.index is not None:
                have[o.fmt].setdefault(o.index, o)
        indexes = sorted(set(s.known(AUDIO)) | set(s.known(EBOOK)) | set(have[AUDIO]) | set(have[EBOOK]))
        p = theme._current  # noqa: SLF001
        t.setRowCount(len(indexes))
        for r, i in enumerate(indexes):
            vol = s.audible.get(i) or s.others.get(i) or s.known(EBOOK).get(i)
            owned = have[AUDIO].get(i) or have[EBOOK].get(i)
            title = (owned.title if owned else "") or (vol.title if vol else "")
            cells = []
            for f in (AUDIO, EBOOK):
                o = have[f].get(i)
                if o is not None:
                    cells.append("✓" + (" ✔" if o.finished else ""))
                elif f in s.formats and i in s.known(f) and not (vol and vol.upcoming):
                    cells.append("missing")
                else:
                    cells.append("")
            where = owned.source if owned else ""
            upcoming = bool(vol and vol.upcoming and not owned)
            row = [fmt_index(i), title, *cells, vol.release if vol else "", where]
            for c, val in enumerate(row):
                it = QTableWidgetItem(val)
                if c == 0:
                    it.setData(Qt.UserRole, (vol.url if vol else "") or (owned.path if owned else ""))
                if "missing" in cells:
                    it.setForeground(QColor(p.problem))
                elif upcoming:
                    it.setForeground(QColor(p.check))
                t.setItem(r, c, it)

    # -- covers ----------------------------------------------------------------------------------------
    def _volume_cover(self) -> None:
        key = self._selected_key()
        s = self.series.get(key) if key else None
        rows = self.volumes.selectionModel().selectedRows()
        if s is None or not rows:
            return
        try:
            i = float(self.volumes.item(rows[0].row(), 0).text())
        except ValueError:
            return
        owned = next((o for o in s.owned if o.index == i), None)
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
        key = self._selected_key()
        s = self.series.get(key) if key else None
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
        key = self._selected_key()
        if key and (s := self.series.get(key)):
            self.check_online([s])

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
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            save_settings(self.settings)
            theme.apply(self.settings.theme)
            self.scan()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt
        ui_settings().setValue("geometry", self.saveGeometry())
        super().closeEvent(event)


def _have(s: Series, fmt: str) -> str:
    n = len(s.have(fmt))
    return str(n) if n else ""


class _Item(QTableWidgetItem):
    """Sorts numbers as numbers ('10' after '9')."""

    def __lt__(self, other) -> bool:  # noqa: D105
        a, b = self.text(), other.text()
        try:
            return float(a or -1) < float(b or -1)
        except ValueError:
            return a.lower() < b.lower()


class SettingsDialog(QDialog):
    def __init__(self, s: Settings, parent=None):
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Settings")
        self.resize(760, 560)
        form = QFormLayout(self)
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
        self.theme = QComboBox()
        self.theme.addItems(list(theme.THEMES))
        self.theme.setCurrentText(s.theme)
        form.addRow("Colours", self.theme)
        hidden = QLabel(f"{len(s.hidden_series)} hidden series")
        b = QPushButton("Show them again")
        b.clicked.connect(lambda: (s.hidden_series.clear(), hidden.setText("0 hidden series")))
        row = QHBoxLayout()
        row.addWidget(hidden)
        row.addWidget(b)
        row.addStretch()
        form.addRow("Hidden", row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

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
        s.use_abs, s.abs_url, s.abs_api_key = self.use_abs.isChecked(), self.abs_url.text().strip().rstrip("/"), \
            self.abs_key.text().strip()
        s.library_folders, s.staging_folders = lines(self.libs), lines(self.staging)
        s.audible_region = self.region.currentText()
        s.use_audible, s.use_anilist, s.use_google = (self.use_audible.isChecked(), self.use_anilist.isChecked(),
                                                      self.use_google.isChecked())
        s.google_books_key = self.google_key.text().strip()
        s.theme = self.theme.currentText()
        self.accept()
