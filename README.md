# Book Collection Checker

See what you have and what's missing in your audiobook and ebook series - a small Windows app for an
[Audiobookshelf](https://www.audiobookshelf.org/) library (and plain library folders).

**Work in progress** - expect rough edges and changes.

## What it does
- **Reads what you own** from your Audiobookshelf libraries (with listening progress), library folders on disk and
  staging folders (e.g. [Book Sorter](https://github.com/Farathim89/audiobook-ebook-sorter)'s sorted folders).
  It only reads - nothing is ever written there.
- **Looks up what each series has**:
  - **Audible** - every volume of the series, with release dates (also upcoming ones)
  - **AniList** - how many volumes a light novel / manga has
  - **Google Books** - ebook volumes Audible doesn't sell (an API key helps)
- **Shows per series** what you have (audio / ebook), what's missing and what's coming, with covers.
  Choose per series which formats you collect - missing books are only counted for those.
- **Release calendar** of every upcoming volume of your series.
- **Copy missing list** - a shopping list on the clipboard.

## Download
Grab the latest [release](https://github.com/Farathim89/book-collection-checker/releases):
- **BookCollectionChecker-portable.zip** - unzip anywhere and run `BookCollectionChecker.exe`; settings and the
  lookup cache stay in the `CollectionChecker-data` folder next to it.
- **BookCollectionChecker.exe** - the same app on its own (settings in your user profile).

No install, no Python. Windows 10/11. SmartScreen may warn because the exe isn't code-signed: *More info → Run anyway*.

On first start it takes the Audiobookshelf address/API key and the folders from Book Sorter's settings if it finds
them; otherwise fill them in under **Settings**.

## From source
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python checker.pyw
```
`build.cmd` makes the exe (PyInstaller).

## License
[MIT](LICENSE) © Farathim
