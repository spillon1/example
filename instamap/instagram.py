"""Talking to Instagram.

Login happens in a real browser window that you control, so your password never
passes through this app. Once you're logged in we keep the session cookies
locally and use the same endpoints the instagram.com website uses to list your
saved collections.
"""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Iterator

import requests

log = logging.getLogger(__name__)

BASE = "https://www.instagram.com"
WEB_APP_ID = "936619743392459"  # public ID the instagram.com web client sends with every API call
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
ALL_SAVED = "all"  # pseudo collection id for "All posts"


class InstagramError(RuntimeError):
    pass


class NotLoggedIn(InstagramError):
    pass


# --------------------------------------------------------------------------- login
def _launch_browser(p, profile_dir: Path):
    """Use the Edge or Chrome already on the computer, so there's no browser to download."""
    opts = dict(headless=False, viewport={"width": 480, "height": 860},
                args=["--disable-blink-features=AutomationControlled"])
    for channel in ("msedge", "chrome", None):
        try:
            return p.chromium.launch_persistent_context(str(profile_dir), channel=channel, **opts)
        except Exception as exc:
            log.info("Couldn't open browser %s: %s", channel or "chromium", exc)
    # Last resort: fetch Playwright's own Chromium once, then try again.
    import subprocess
    import sys
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
    return p.chromium.launch_persistent_context(str(profile_dir), **opts)


def browser_login(profile_dir: Path, session_file: Path, timeout: int = 600) -> dict:
    """Open a browser window at instagram.com and wait until you've logged in.

    Uses a persistent browser profile, so next time you're usually still signed in.
    """
    from playwright.sync_api import sync_playwright  # imported lazily, it's heavy

    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = _launch_browser(p, profile_dir)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(f"{BASE}/accounts/login/", wait_until="domcontentloaded")
            deadline = time.time() + timeout
            while time.time() < deadline:
                cookies = {c["name"]: c["value"] for c in ctx.cookies(BASE)}
                if cookies.get("sessionid") and cookies.get("ds_user_id"):
                    # Give the site a moment to finish setting csrftoken etc.
                    page.wait_for_timeout(2000)
                    cookies = {c["name"]: c["value"] for c in ctx.cookies(BASE)}
                    # Later requests use the same browser identity the login came from.
                    cookies["_ua"] = page.evaluate("navigator.userAgent")
                    session_file.write_text(json.dumps(cookies))
                    session_file.chmod(0o600)
                    return cookies
                page.wait_for_timeout(1000)
            raise NotLoggedIn("Timed out waiting for Instagram login")
        finally:
            ctx.close()


def load_session(session_file: Path) -> dict | None:
    if not session_file.exists():
        return None
    try:
        return json.loads(session_file.read_text())
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- client
class InstagramClient:
    def __init__(self, cookies: dict, delay: float = 3.0, session: requests.Session | None = None):
        if not cookies or "sessionid" not in cookies:
            raise NotLoggedIn("No Instagram session. Log in first.")
        self.delay = delay
        self.http = session or requests.Session()
        cookies = dict(cookies)
        user_agent = cookies.pop("_ua", None) or USER_AGENT
        for name, value in cookies.items():
            self.http.cookies.set(name, value, domain=".instagram.com")
        self.http.headers.update({
            "User-Agent": user_agent,
            "X-IG-App-ID": WEB_APP_ID,
            "X-CSRFToken": cookies.get("csrftoken", ""),
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{BASE}/",
            "Accept": "*/*",
        })
        self._last_call = 0.0

    def _pause(self) -> None:
        wait = self.delay + random.uniform(0, self.delay / 2) - (time.time() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.time()

    def _get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(5):
            self._pause()
            resp = self.http.get(f"{BASE}{path}", params=params, timeout=30)
            if resp.status_code == 429:
                backoff = 60 * (attempt + 1)
                log.warning("Instagram rate limit hit, waiting %ss", backoff)
                time.sleep(backoff)
                continue
            if resp.status_code in (401, 403):
                raise NotLoggedIn("Instagram session expired. Log in again.")
            try:
                data = resp.json()
            except ValueError:
                # HTML back usually means a redirect to the login page.
                raise NotLoggedIn("Instagram returned a login page. Log in again.") from None
            if data.get("message") in ("login_required", "checkpoint_required", "challenge_required"):
                raise NotLoggedIn(f"Instagram says {data['message']}. Open instagram.com in a browser, "
                                  "clear any security check, then log in again here.")
            if resp.status_code >= 400:
                raise InstagramError(f"Instagram error {resp.status_code}: {data.get('message')}")
            return data
        raise InstagramError("Instagram kept rate limiting us; try again later")

    def whoami(self) -> str | None:
        data = self._get("/api/v1/accounts/current_user/", {"edit": "true"})
        return (data.get("user") or {}).get("username")

    def collections(self) -> list[dict]:
        out = [{"id": ALL_SAVED, "name": "All saved posts", "count": None}]
        max_id = None
        while True:
            params = {"collection_types": '["MEDIA","ALL_MEDIA_AUTO_COLLECTION"]'}
            if max_id:
                params["max_id"] = max_id
            data = self._get("/api/v1/collections/list/", params)
            for c in data.get("items", []):
                if c.get("collection_type") == "ALL_MEDIA_AUTO_COLLECTION":
                    continue
                out.append({"id": str(c["collection_id"]), "name": c.get("collection_name"),
                            "count": c.get("collection_media_count")})
            if not data.get("more_available") or not data.get("next_max_id"):
                return out
            max_id = data["next_max_id"]

    def saved_media(self, collection_id: str) -> Iterator[dict]:
        path = ("/api/v1/feed/saved/posts/" if collection_id == ALL_SAVED
                else f"/api/v1/feed/collection/{collection_id}/posts/")
        max_id = None
        while True:
            data = self._get(path, {"max_id": max_id} if max_id else None)
            for item in data.get("items", []):
                media = item.get("media", item)
                parsed = parse_media(media, collection_id)
                if parsed:
                    yield parsed
            if not data.get("more_available") or not data.get("next_max_id"):
                return
            max_id = data["next_max_id"]

    def download(self, url: str, dest: Path) -> Path:
        self._pause()
        with self.http.get(url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(1 << 16):
                    fh.write(chunk)
        return dest


# --------------------------------------------------------------------------- parsing
def _best_image(item: dict) -> str | None:
    candidates = (item.get("image_versions2") or {}).get("candidates") or []
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.get("width", 0) or 0).get("url")


def _best_video(item: dict) -> str | None:
    versions = item.get("video_versions") or []
    if not versions:
        return None
    # The smallest decent version is plenty for OCR and speech, and much faster to download.
    usable = [v for v in versions if (v.get("width") or 0) >= 480] or versions
    return min(usable, key=lambda v: v.get("width", 0) or 0).get("url")


def parse_media(m: dict, collection_id: str | None = None) -> dict | None:
    media_id = str(m.get("pk") or m.get("id") or "")
    if not media_id:
        return None
    items = m.get("carousel_media") or [m]
    media = []
    for item in items:
        video = _best_video(item)
        if video:
            media.append({"kind": "video", "url": video})
        else:
            image = _best_image(item)
            if image:
                media.append({"kind": "image", "url": image})

    location = None
    loc = m.get("location")
    if loc and loc.get("name"):
        location = {
            "name": loc.get("name"),
            "lat": loc.get("lat"),
            "lon": loc.get("lng"),
            "address": loc.get("address") or None,
            "city": loc.get("city") or None,
            "pk": str(loc.get("pk") or ""),
        }

    return {
        "media_id": media_id,
        "shortcode": m.get("code"),
        "collection_id": collection_id,
        "username": (m.get("user") or {}).get("username"),
        "caption": (m.get("caption") or {}).get("text") or "",
        "ig_location": location,
        "media_type": m.get("media_type"),
        "media": media,
        "thumbnail": _best_image(items[0]) if items else None,
        "taken_at": m.get("taken_at"),
    }
