<div align="center">

<img src="https://github.com/Lseauk/EnvyUI/blob/main/assets/icon.ico" width="80" alt="EnvyUI">

# EnvyUI

**A Windows GUI for TwinVine's (Envied)**

![Windows](https://img.shields.io/badge/Platform-Windows%2010%2F11-blue?style=flat-square)
![Version](https://img.shields.io/badge/Version-1.0.0%20Beta-green?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.12%2F3.13-blue?style=flat-square)

</div>

---

## Credits

This App is built on top of **[TwinVine](https://github.com/vinefeeder/TwinVine)** — an open-source project created by **vinefeeder / A_n_g_e_l_a**.

TwinVine combines VineFeeder (a service scraper and download manager) with Envied (a DRM decryption and media processing engine) to download content from a range of streaming services. Full credit for the underlying technology goes to the original authors — without their work this launcher would not exist.

---

## Why This Project Exists

TwinVine is a powerful tool but requires some comfort with the command line to set up and use. I wanted to make it a little easier for me to install and use — no terminal, no technical knowledge, just a clean window where you click a service, pick your episodes, and download.

EnvyUI handles everything automatically: installing all required tools, setting up the Python environment, and providing a straightforward GUI that wraps the entire TwinVine workflow.

> **⚠ Windows Only** — EnvyUI is a Windows 10/11 application only.

**This is not a replacement for the original project**
- For more complex downloads and use of other services I would strongly recommend the original developer's project above. You can still call Envied from the command line for services not listed in EnvyUI or for more complex downloads, but this is not supported so your mileage may vary.
---

## Known Issues & Quirks

This release. The following known issues exist — contributions and bug reports are welcome.

**1. CWTV** When using browse by category it will list all shows rather than genres/categories as at this time we've been unable to get the data from CWTV, keyword search for a particular show would be a better option at present.
**2. Download Log Panel** Sometimes the live data will disappear for a split second every now and then, the download is still ongoing just a minor quirk that is actively being fixed.
**3. Download Log Panel** Sometimes it may take a short while for the live download data to show in the download log panel so please give it a little while to appear before you cancel a download.
**4. Browser by Category** When fetching information from a service can sometime take a little bit of time to get, especially if a list is large and it could also depend on your connection speed, you will see an error if it fails to get any results.
**5. IMDBApi Error in Download Log Panel** imdbapi.dev is down or unavailable, which will show as an error when downloading, while this does not affect the actual download we added a fix for this, see the help page of the app to address this issue.

---

## Pre-requirements

Before installing, you will need:

- **Windows 10 or 11** (64-bit)
- **Python 3.12 or 3.13 or 3.14** — download the Windows installer (64-bit) from the official releases page:
  - [Python 3.14](https://www.python.org/downloads/release/python-3145/)
  - [Python 3.13](https://www.python.org/downloads/release/python-3130/)
  - [Python 3.12](https://www.python.org/downloads/release/python-3120/)
  
  - During installation tick **"Add Python to PATH"**
  - Do **not** use the Microsoft Store version of Python or the install manager from Python at this time.

Everything else (Git, FFmpeg, MKVToolNix, Bento4, Shaka Packager, N_m3u8DL-RE, dovi_tool, hdr10plus_tool, CCExtractor, SubtitleEdit, and all Python packages) is downloaded and installed automatically by the app.

- **Services Credentials** - Some services like All4 require login details, username/password before they will download, please see the help page for more details after install.
- **VPN Use** - Some services may require the use of a VPN depending on your location.
---

## Installation

Download and unzip `EnvyUI.zip`, then double-click `EnvyUI.bat` to launch.

---

On first launch `EnvyUI.bat` automatically installs the following into your system Python before the app opens — you will see a brief console window while this happens:

- **PyQt6** — the UI framework
- **PyQt6-WebEngine** — powers the in-app terminal panel
- **pywinpty** — enables real-time download progress output
- **certifi** — SSL certificates for secure connections
- **uv** — the Python package manager used to build the EnvyCore environment

Once the app opens, click **Install / Update → Install EnvyUI Tools** and wait for the setup to complete. This downloads around 500MB of tools and takes 2–5 minutes depending on your connection. Progress is shown in the Log tab.

---

## How to Use

There are two ways to start a download:

**Option 1 — Search box first**
Type a keyword or paste a URL into the **URL or Search** box, then click a service button. The search runs immediately against that service.

**Option 2 — Service button first**
Click a service button directly and choose from four actions:
- **Search by keyword** — type a show name to find it
- **Greedy Search by URL** — paste a show page URL to fetch all available content
- **Download by URL** — paste a direct episode URL to download immediately
- **Browse by Category** — browse the service's categories

Either way, once results appear:

1. Select the series you want from the list
2. Tick the episodes you want and click **Confirm**
3. The download begins automatically — progress is shown in the panel below

> For more detail on all options and features, check the **Help** page inside the app.


### Batch Mode

Toggle **Batch Mode** on to queue episodes from multiple shows before downloading them all at once. The sidebar shows how many episodes are queued. Click **Run Batch** when ready.

---

## Screenshots

### First Run
![First Run](https://github.com/Lseauk/EnvyUI/blob/main/Images/01%20-%20First%20Run.png?raw=true)

### Install / Update
![Initial Install](https://github.com/Lseauk/EnvyUI/blob/main/Images/02%20-%20Install.png?raw=true)

### Install Complete
![Install Complete](https://github.com/Lseauk/EnvyUI/blob/main/Images/03%20-%20Install%2002.png?raw=true)

### Ready to Use
![Ready To Use](https://github.com/Lseauk/EnvyUI/blob/main/Images/04%20-%20Main.png?raw=true)
![Ready To Use](https://github.com/Lseauk/EnvyUI/blob/main/Images/04%20-%20Main%2002.png?raw=true)

### Service Button Actions
![Service Button Action](https://github.com/Lseauk/EnvyUI/blob/main/Images/05%20-%20Service%20Button%20Click.png?raw=true)

### Searching for a Show
![Show Selection](https://github.com/Lseauk/EnvyUI/blob/main/Images/06%20-%20Key%20Word%20Search.png?raw=true)

### Series Selection
![Series Selection](https://github.com/Lseauk/EnvyUI/blob/main/Images/07%20-%20Season%20and%20Episode%20selection.png?raw=true)

### Live Envied Download
![Live Envied Download](https://github.com/Lseauk/EnvyUI/blob/main/Images/08%20-%20Live%20Envied%20Download%20Feed.png?raw=true)

### Quality selection, Subtitles, Slow mode
![Quality Selection](https://github.com/Lseauk/EnvyUI/blob/main/Images/09%20-%20Fetch-Quality.png?raw=true)

### Extended Services
![Extended Services](https://github.com/Lseauk/EnvyUI/blob/main/Images/10%20-%20Extended%20Services.png?raw=true)

### Log Panel
![Log Panel](https://github.com/Lseauk/EnvyUI/blob/main/Images/12%20-%20Log%20Panel.png?raw=true)

### Help
![Help](https://github.com/Lseauk/EnvyUI/blob/main/Images/13%20-%20Help%20Page.png?raw=true)

### About
![About](https://github.com/Lseauk/EnvyUI/blob/main/Images/14%20-%20About.png?raw=true)

---

## Supported Services

> **⚠ While some services on the app may have paid for or subscription plans we can only offer support or bug reports for Free-to-air content only as we are unable to test anything other than services that offer Free-to-air content and while envied lists over 60 services we only list services that we have been able to test and are known to work with the app, you can of course still use envied from a terminal window inside the EnvyCore folder if you're familiar with envied commands structure. All services listed below are free to watch without a subscription (though some require a free account for login).**

**Main page:** ALL4 · BBC iPlayer · ITVX · My5 · U (UKTV) · RTE · STV · TPTV · Rakuten TV · Tubi · Pluto TV · VM Play (IE) · TVNZ · ThreeNow (NZ) · ABC iView (AU) · 7plus (AU) · 9Now (AU) · 10play (AU) · SBS On Demand (AU) · Roku (US) · CBS (US) · NBC · PBS · The CW (US) · Crave · CBC Gem · Plex

**Extended Services page:** Blaze TV, NFBC, RTE+, NPO, ARD Mediathek, NRK

---

## How EnvyUI Differs from TwinVine

EnvyUI bundles a snapshot of envied (the download engine from TwinVine) and adds a Windows GUI on top. It is not a fork of TwinVine — it is a separate project that includes envied directly. This section documents what we do differently and why, so anyone comparing the two knows what to expect.

### What was removed

**VineFeeder** has been removed entirely. In TwinVine, VineFeeder is a second GUI layer that sits in front of envied and drives it. EnvyUI replaces that role with its own launcher (`envy_launcher.py`), which calls envied directly using `uv run envied dl`. This means there is no dependency on VineFeeder updates and no risk of GUI/engine version mismatches.

**Auto-update from GitHub** has been removed. TwinVine pulls updates from external repositories automatically. EnvyUI updates are controlled releases only — you get exactly what has been tested and bundled.

### Download folder structure

EnvyUI organises downloads into a Plex / Jellyfin / Kodi compatible layout automatically:

- **TV:** `Downloads/Show Name (Year)/Season 01/Show Name S01E01 Episode Name.mkv`
- **Movies:** `Downloads/Movie Name (Year)/Movie Name (Year).mkv`

The show folder always uses the series premiere year (e.g. Death in Paradise always goes in `Death in Paradise (2011)/` regardless of which season you download). TwinVine drops all files flat into the download directory.

### Metadata tagging

EnvyUI correctly propagates the IMDB ID from the metadata provider chain through to the MKV tag writer. In the upstream version the IMDB ID was returned by the provider but silently discarded — only the TMDB ID was passed along. Downloaded files from EnvyUI carry correct IMDB, TMDB, and TVDB tags where available.

### BBC iPlayer — HLG / UHD handling

EnvyUI handles BBC HLG downloads via the `--range HLG` CLI argument with automatic retry logic in the launcher. The old `BbcLoader.check_uhd` monkeypatch used in earlier versions is not present — the current approach is cleaner and does not require patching envied internals at runtime.

### Patches applied to envied services

The following service-level changes are present in EnvyUI but not currently in the TwinVine upstream. These are intentional fixes — do not revert them when syncing.

| Service | Change | Why |
|---------|--------|-----|
| **PBS** | Next.js App Router RSC fix | TwinVine's version is broken on the current PBS site — our fix handles the updated page structure |
| **VM** | `mp4decrypt` override + fTTML subtitle filter | Upstream does not handle fTTML subtitles correctly for this service |
| **RKTN** | URL parsing improvements + safety defaults | More robust handling of Rakuten TV URL variations |
| **TUBI** | `h264_720p` and `h265_720p` added to `limit_resolutions[]` API param | UK region compatibility — without this, 720p options are not returned |
| **NINE** | `_is_caption_track()` filter + `_first_source()` HTTPS helper | Prevents thumbnail/metadata tracks being added as subtitles; prefers HTTPS sources with fallback |

### Patches applied at install time

When you click **Install / Update → Install EnvyUI Tools**, the installer automatically patches several files within the bundled EnvyCore. These are applied once and backed up (`.bak` files) before any change is made. They are re-checked and skipped if already applied.

**Python version compatibility**
- `pyproject.toml` files — the `requires-python` upper bound (e.g. `<=3.12`) is widened so the environment builds correctly on Python 3.13 and 3.14 without manual editing.
- `pywinpty` version pin — the `<3` upper bound is removed so Python 3.14 can install the newer 3.x release that added 3.14 support.
- `brotli` → `brotlicffi` — the `brotli` package requires C++ Build Tools to compile on Python 3.14+. It is swapped to `brotlicffi`, a pure-Python equivalent that installs without a compiler.
- `utilities.py` — the `FPS` class uses `ast.Num` which was removed in Python 3.14. A `visit_Constant` method is added alongside `visit_Num` so frame-rate parsing works on all Python versions.

**Download engine**
- `commands/dl.py` — the per-episode summary message `"Processed all titles in Xm Xs"` is shortened to `"Processed in Xm Xs"`. The original wording is misleading when downloading multiple episodes.

**Metadata providers**
- `core/providers/__init__.py` — OMDb is added to the metadata provider chain and the order is corrected to: IMDBApi → TMDB → OMDb → Simkl. The upstream order puts Simkl before TMDB, which is slower and less reliable as a metadata source.
- `core/config.py` — the `omdb_api_key` field is added to the config class so envied can read an OMDb API key from `envied.yaml`. Without this the OMDb provider has no way to retrieve its key.

**Services**
- `services/TEN/__init__.py` — three fixes: (1) removes a `config.downloader` check that was dropped in a later envied version and raises a false error, (2) forces `title.language = "en"` at the start of `get_tracks` because the language set during episode construction is sometimes lost due to Python bytecode caching, (3) adds `OnSegmentFilter` to skip Google DAI ad segments so they don't interrupt the download.
- `services/CWTV/__init__.py` — same language guard as TEN: forces `title.language = "en"` at the start of `get_tracks`. Without this the core track selector falls back to `"orig"` and skips the video track entirely.
- `core/manifests/hls.py` — adds an empty-batch guard for Google DAI ad-break streams. When an ad segment is skipped, the AES key-change trigger can fire with a negative range length, crashing the download with "None of the segment files exist".
- `services/PBS/__init__.py` — rewrites `_fetch_video_bridge` for the current PBS website. PBS migrated to Next.js App Router and stream URLs now live in RSC payload chunks rather than the old `window.videoBridge` object. The upstream version is broken on the current site.

**Config**
- `envied.yaml` — on first install, `envied-working-example.yaml` is copied to `envied.yaml`. The `vaults` and `cookies` paths are then rewritten to absolute paths so they resolve correctly regardless of which directory envied is launched from.

---

## Contributing & Feedback

EnvyUI has so far only been tested by a small number of users. If you find a bug, have a suggestion, or want to contribute, please:

- **Open an issue** on the [GitHub Issues](https://github.com/Lseauk/EnvyUI/issues) page — bug reports, feature requests, and general feedback are all welcome
- **Submit a pull request** if you have a fix or improvement you'd like to contribute
- **Test on different services** — not all supported services have been fully tested, so reports on what works and what doesn't are particularly helpful

Your feedback helps make it better for everyone.

---

## Disclaimer

This tool is intended for personal use only. You are responsible for ensuring you have the right to download any content you access. The authors of EnvyUI take no responsibility for how this software is used.
