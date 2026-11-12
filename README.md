# InstaMap

Point it at one of your saved Instagram collections (e.g. "Travel"). It goes through every
saved post and reel in the background, works out which places are mentioned, puts them on
a map, and helps you get the ones you want into Google Maps.

Everything runs on your own computer and nothing costs money: no paid APIs, no API keys.

## What it reads in each post

| Source | How | Notes |
|---|---|---|
| Instagram location tag | from Instagram directly | already has coordinates, most reliable |
| Caption | text | catches "📍 Place", "Location: ...", numbered lists |
| What's said in the video | [faster-whisper](https://github.com/SYSTRAN/faster-whisper), local speech-to-text | model downloads once (~500 MB for `small`) |
| Text shown on screen | frames sampled with ffmpeg, read with [RapidOCR](https://github.com/RapidAI/RapidOCR) | |
| Picking out place names | a local LLM via [Ollama](https://ollama.com) if installed, otherwise spaCy plus rules | Ollama makes a big difference, see below |
| Coordinates | OpenStreetMap ([Nominatim](https://nominatim.org), Photon as backup) | free, 1 lookup/second, cached |

## Install on Windows (easiest)

1. Download the zip, right-click it and choose **Extract All**.
2. Open the extracted folder and double-click **Start InstaMap**.
   If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**.
3. The first time, it installs what it needs (Python included, if you don't have it). That takes
   5 to 15 minutes. After that it starts straight away.
4. The app opens in your browser. Click **Log in to Instagram**, log in in the window that
   appears (it uses Microsoft Edge or Chrome), pick your saved folder, and leave it to work.

Keep the black InstaMap window open while it runs (minimise it if you like). Closing it stops InstaMap.
Double-click **Start InstaMap** any time to open it again; it picks up where it left off.

## Install manually (Mac, Linux, or if you prefer)

You need Python 3.10 or newer. ffmpeg comes bundled.

```bash
git clone <this repo> instamap && cd instamap
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[ml]"
playwright install chromium     # only needed if you have neither Chrome nor Edge
python -m spacy download en_core_web_sm
```

**Strongly recommended:** install [Ollama](https://ollama.com/download) and pull a model. It's free
and runs locally; it's far better than the rules at telling "we had dinner at Cervejaria Ramiro" apart
from noise.

```bash
ollama pull qwen2.5:7b      # ~5 GB. On a lighter laptop try llama3.2:3b and set INSTAMAP_OLLAMA_MODEL=llama3.2:3b
```

InstaMap detects Ollama automatically when it's running.

## Use it

```bash
instamap
```

This opens http://127.0.0.1:8765.

1. **Log in to Instagram.** A browser window opens on instagram.com, and you log in there
   yourself (2FA works as normal). Your password never goes through InstaMap. Only the session
   cookie is kept, in `~/.instamap/`.
2. **Pick the collection** to watch, or "All saved posts".
3. **Walk away.** You can close the browser tab. As long as the `instamap` process is running it
   keeps working through the posts, and it checks the collection for new saves every 6 hours.
   Progress is saved, so if you quit or restart it carries on where it stopped.
4. **Review.** Places appear in the list and on the map as they're found. Each one shows where it
   came from (caption, said in video, on screen, location tag) with the quote, so you can tell
   at a glance whether it's right. Click "Not a place" on mistakes. "Fix location" re-searches
   the map if a pin landed in the wrong spot.
5. **Add to Google Maps.** Tick the places you want and press *Add to Google Maps…*.

### Getting places into your Google account

Google has no API that lets other apps add to your personal saved places, so there are two free routes:

- **One by one into a Saved list.** "Open next place" opens each place's Google Maps page. Tap
  *Save*, pick a list (Want to go, Favourites, or your own), and come back for the next. It's one tap per place.
- **All at once as a map.** Download the KML (or CSV) and import it at
  [Google My Maps](https://www.google.com/maps/d/) (*Create a new map → Import*). Places are grouped
  into layers by category, and each one links back to its Instagram post. The map then shows up in
  the Google Maps app under *You → Saved → Maps*.

### It runs on a computer, not on your phone

InstaMap needs a Mac, Windows or Linux computer: it does the video analysis there.
`127.0.0.1` means "this device", so typing it into your phone's browser looks for the app on
the phone and fails with "refused to connect".

To check progress or pick places from your phone, start it on the computer with

```bash
instamap serve --lan
```

and open the address it prints (something like `http://192.168.1.23:8765`) on your phone while
both are on the same Wi-Fi. Do the Instagram login step on the computer, since that's where the
login window opens. There's no password on the page, so only use `--lan` on a network you trust.

### Running it in the background

Leave `instamap` running in a terminal, or start it detached:

```bash
nohup instamap serve --no-browser > ~/.instamap/log.txt 2>&1 &
```

There's also a headless mode that does one pass and exits:

```bash
instamap collections          # list your collections
instamap run "Travel"         # sync and analyse everything in "Travel"
```

## Settings

Set these as environment variables:

| Variable | Default | |
|---|---|---|
| `INSTAMAP_WHISPER_MODEL` | `small` | `base` is faster, `medium` is more accurate |
| `INSTAMAP_OLLAMA_MODEL` | `qwen2.5:7b` | any Ollama model |
| `INSTAMAP_FRAME_INTERVAL` | `1.5` | seconds between frames read for on-screen text |
| `INSTAMAP_IG_DELAY` | `3` | seconds between Instagram requests |
| `INSTAMAP_KEEP_MEDIA` | `0` | `1` keeps downloaded videos in `~/.instamap/media` |
| `INSTAMAP_DATA_DIR` | `~/.instamap` | where the database, session and downloads live |

## Things to know

- **"Stories" vs saved posts.** Instagram only lets you save posts and reels into collections.
  Stories you've added to Highlights aren't supported yet.
- **Instagram's rules.** This uses the same internal endpoints the instagram.com website uses,
  with your own logged-in session and only for your own saves. It goes slowly on purpose (a few
  seconds between requests), but automated access is against Instagram's terms, so there's a small
  chance they'll ask you to verify your account. If the app says "Needs login", open instagram.com
  in a normal browser, clear any security check, then log in again in InstaMap.
- **Accuracy.** Location tags and "📍" lines are reliable. Places only *spoken* in a video depend on
  Whisper hearing the name right, which is harder with unusual names over loud music. Anything the
  map can't find is kept under "not found on map" so you can fix it by hand rather than losing it.
- **Speed.** Roughly 20 to 60 seconds per reel on a modern laptop, mostly speech-to-text. A
  collection of 200 reels takes an hour or two.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Map view uses [Leaflet](https://leafletjs.com) (bundled in `instamap/static/vendor`, BSD-2 and MIT
licences) with OpenStreetMap tiles.
