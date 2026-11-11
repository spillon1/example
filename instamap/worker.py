"""Background worker: fetches your saved collection and analyses posts one by one.

It runs in its own thread inside the server process, so you can close the browser
tab and come back later. Progress lives in SQLite, so a restart picks up where it left off.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback

from .config import Settings
from .db import Database
from .geocode import Geocoder
from .instagram import InstagramClient, InstagramError, NotLoggedIn, load_session
from .pipeline import process_post

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, db: Database, settings: Settings, resync_hours: float = 6.0):
        self.db = db
        self.settings = settings
        self.resync_hours = resync_hours
        self.geocoder = Geocoder(db, settings.geocoder_user_agent)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._sync_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = "idle"
        self.message = ""
        self.current: str | None = None

    # ---- control ---------------------------------------------------------
    def start(self) -> None:
        self.db.reset_stuck_posts()
        self._thread = threading.Thread(target=self._run, name="instamap-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request_sync(self) -> None:
        self._sync_requested.set()
        self._wake.set()

    def nudge(self) -> None:
        self._wake.set()

    def status(self) -> dict:
        return {
            "state": self.state, "message": self.message, "current": self.current,
            "counts": self.db.post_counts(), "collection": self.db.get("collection"),
            "last_sync": self.db.get("last_sync"),
        }

    def _set(self, state: str, message: str = "") -> None:
        self.state, self.message = state, message
        if message:
            log.info("%s: %s", state, message)

    def client(self) -> InstagramClient:
        return InstagramClient(load_session(self.settings.session_file) or {}, delay=self.settings.ig_delay)

    # ---- loop ------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except NotLoggedIn as exc:
                self._set("needs_login", str(exc))
                self._sync_requested.clear()
            except Exception as exc:
                log.error("Worker error: %s\n%s", exc, traceback.format_exc())
                self._set("error", str(exc))
            self._wake.wait(timeout=60)
            self._wake.clear()

    def _sync_due(self) -> bool:
        collection = self.db.get("collection")
        if not collection:
            return False
        if self._sync_requested.is_set():
            return True
        last = self.db.get("last_sync") or 0
        return self.resync_hours > 0 and time.time() - last > self.resync_hours * 3600

    def _tick(self) -> None:
        if self._sync_due():
            self._sync_requested.clear()
            self.sync()
        self.process_queue()
        if self.state not in ("needs_login", "error"):
            self._set("idle", "Waiting for new saves" if self.db.get("collection") else "Pick a collection")

    def sync(self) -> int:
        collection = self.db.get("collection")
        self._set("syncing", f"Listing posts in '{collection['name']}'")
        new = 0
        client = self.client()
        for i, post in enumerate(client.saved_media(collection["id"]), 1):
            if self.db.upsert_post(post):
                new += 1
            self.message = f"Listing posts in '{collection['name']}': {i} seen, {new} new"
            if self._stop.is_set():
                break
        self.db.set("last_sync", time.time())
        return new

    def process_queue(self) -> None:
        if not self.db.post_counts()["pending"]:
            return
        client = self.client()  # before claiming a post, so a login problem can't strand one
        while not self._stop.is_set():
            post = self.db.next_pending_post()
            if not post:
                break
            counts = self.db.post_counts()
            done = counts["done"] + counts["error"]
            self.current = post.get("shortcode")
            self._set("processing", f"Analysing post {done + 1} of {counts['total']} "
                                    f"(@{post.get('username') or '?'})")
            try:
                process_post(post, db=self.db, geocoder=self.geocoder,
                             downloader=client.download, settings=self.settings)
            except NotLoggedIn:
                self.db.execute("UPDATE posts SET status='pending' WHERE id=?", (post["id"],))
                raise
            except Exception as exc:
                log.warning("Post %s failed: %s", post.get("shortcode"), exc)
                msg = str(exc)
                if isinstance(exc, InstagramError) or "403" in msg:
                    msg += " (media link may have expired; 'Sync now' then 'Retry failed' refreshes it)"
                self.db.finish_post(post["id"], error=msg[:500])
            finally:
                self.current = None
