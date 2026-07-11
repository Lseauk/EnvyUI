<div align="center">

<img src="https://github.com/Lseauk/EnvyUI/blob/main/assets/icon.ico" width="80" alt="EnvyUI">

# EnvyUI

**A Windows GUI for TwinVine (Envied)**

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

**2. Download Log Panel** Sometimes the live data will disappear for a slit second every now and then, the download is still ongoing just a minor quirk that is actively being fixed.

**3. Download Log Panel** Sometimes it may take a short while for the live download data to show in the download log panel so please give it a little while to appear before you cancel a download.

**4. Browser by Category** When fetching information from a service can sometime take a little bit of time to get, especially if a list is large and it could also depend on your connection speed, you will see an error if it fails to get any results.

**5. IMDBApi Error in Download Log Panel** imdbapi.dev is down or unavailable, which will show as an error when downloading, while this does not affect the actual download we added a fix for this, see the changelog and the help page of the app to address this issue.

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

## Contributing & Feedback

EnvyUI has so far only been tested by a small number of users. If you find a bug, have a suggestion, or want to contribute, please:

- **Open an issue** on the [GitHub Issues](https://github.com/Lseauk/EnvyUI/issues) page — bug reports, feature requests, and general feedback are all welcome
- **Submit a pull request** if you have a fix or improvement you'd like to contribute
- **Test on different services** — not all supported services have been fully tested, so reports on what works and what doesn't are particularly helpful

Your feedback helps make it better for everyone.

---

## Disclaimer

This tool is intended for personal use only. You are responsible for ensuring you have the right to download any content you access. The authors of EnvyUI take no responsibility for how this software is used.
