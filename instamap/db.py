"""SQLite storage. One file, safe to share between the web server and the worker thread."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY,
    media_id TEXT UNIQUE NOT NULL,
    shortcode TEXT,
    collection_id TEXT,
    username TEXT,
    caption TEXT,
    ig_location TEXT,          -- JSON of Instagram's own location tag, if any
    media_type INTEGER,
    media TEXT,                -- JSON list of {"kind": "video"|"image", "url": ...}
    thumbnail TEXT,
    taken_at INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending | processing | done | error
    error TEXT,
    transcript TEXT,
    ocr_text TEXT,
    added_at REAL,
    processed_at REAL
);
CREATE INDEX IF NOT EXISTS posts_status ON posts(status);

CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    query TEXT,
    address TEXT,
    city TEXT,
    country TEXT,
    category TEXT,
    lat REAL,
    lon REAL,
    status TEXT NOT NULL DEFAULT 'suggested',  -- suggested | approved | rejected | saved
    notes TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS place_sources (
    place_id INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    method TEXT NOT NULL,       -- ig_location | caption | transcript | ocr | llm
    evidence TEXT,
    confidence REAL,
    PRIMARY KEY (place_id, post_id, method)
);

CREATE TABLE IF NOT EXISTS geocode_cache (
    query TEXT PRIMARY KEY,
    result TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def place_key(name: str, lat: float | None, lon: float | None) -> str:
    """Two mentions are the same place if the name matches and they're within ~100m."""
    if lat is None or lon is None:
        return f"{normalise(name)}|unresolved"
    return f"{normalise(name)}|{round(lat, 3)}|{round(lon, 3)}"


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    # ---- low level -------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # ---- key/value -------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # ---- posts -----------------------------------------------------------
    def upsert_post(self, post: dict) -> bool:
        """Insert a post if new. Returns True when it was new."""
        cur = self.execute(
            """INSERT OR IGNORE INTO posts
               (media_id, shortcode, collection_id, username, caption, ig_location,
                media_type, media, thumbnail, taken_at, added_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                post["media_id"], post.get("shortcode"), post.get("collection_id"), post.get("username"),
                post.get("caption"), json.dumps(post.get("ig_location")) if post.get("ig_location") else None,
                post.get("media_type"), json.dumps(post.get("media", [])), post.get("thumbnail"),
                post.get("taken_at"), time.time(),
            ),
        )
        if cur.rowcount == 0:
            # Already known: refresh the (expiring) CDN URLs so a retry can still download.
            self.execute(
                "UPDATE posts SET media=?, thumbnail=? WHERE media_id=?",
                (json.dumps(post.get("media", [])), post.get("thumbnail"), post["media_id"]),
            )
            return False
        return True

    def next_pending_post(self) -> dict | None:
        with self._lock:
            row = self.one("SELECT * FROM posts WHERE status='pending' ORDER BY id LIMIT 1")
            if row:
                self.execute("UPDATE posts SET status='processing' WHERE id=?", (row["id"],))
            return row

    def finish_post(self, post_id: int, *, error: str | None = None, transcript: str | None = None,
                    ocr_text: str | None = None) -> None:
        self.execute(
            "UPDATE posts SET status=?, error=?, transcript=?, ocr_text=?, processed_at=? WHERE id=?",
            ("error" if error else "done", error, transcript, ocr_text, time.time(), post_id),
        )

    def reset_stuck_posts(self) -> None:
        """Anything left 'processing' after a crash or restart goes back in the queue."""
        self.execute("UPDATE posts SET status='pending' WHERE status='processing'")

    def retry_failed(self) -> int:
        return self.execute("UPDATE posts SET status='pending', error=NULL WHERE status='error'").rowcount

    def post_counts(self) -> dict:
        rows = self.query("SELECT status, COUNT(*) AS n FROM posts GROUP BY status")
        counts = {"pending": 0, "processing": 0, "done": 0, "error": 0}
        counts.update({r["status"]: r["n"] for r in rows})
        counts["total"] = sum(counts.values())
        return counts

    # ---- places ----------------------------------------------------------
    def add_place(self, *, post_id: int, name: str, method: str, evidence: str, confidence: float,
                  geo: dict | None, query: str | None = None, category: str | None = None) -> int:
        lat = geo.get("lat") if geo else None
        lon = geo.get("lon") if geo else None
        key = place_key(name, lat, lon)
        with self._lock:
            self.execute(
                """INSERT OR IGNORE INTO places
                   (key, name, query, address, city, country, category, lat, lon, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (key, name, query, geo.get("address") if geo else None, geo.get("city") if geo else None,
                 geo.get("country") if geo else None, category, lat, lon, time.time()),
            )
            place_id = self.one("SELECT id FROM places WHERE key=?", (key,))["id"]
            self.execute(
                """INSERT INTO place_sources(place_id, post_id, method, evidence, confidence)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(place_id, post_id, method) DO UPDATE SET
                     evidence=excluded.evidence, confidence=MAX(confidence, excluded.confidence)""",
                (place_id, post_id, method, evidence[:500], confidence),
            )
            return place_id

    def list_places(self) -> list[dict]:
        places = self.query("SELECT * FROM places ORDER BY country, city, name")
        sources = self.query(
            """SELECT s.place_id, s.method, s.evidence, s.confidence,
                      p.shortcode, p.username, p.thumbnail
               FROM place_sources s JOIN posts p ON p.id = s.post_id"""
        )
        by_place: dict[int, list[dict]] = {}
        for s in sources:
            by_place.setdefault(s.pop("place_id"), []).append(s)
        for place in places:
            place["sources"] = by_place.get(place["id"], [])
            place["confidence"] = max((s["confidence"] or 0 for s in place["sources"]), default=0)
        return places

    def update_place(self, place_id: int, fields: dict) -> None:
        allowed = {"name", "status", "notes", "lat", "lon", "address", "city", "country", "category", "query"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE places SET {sets} WHERE id=?", (*fields.values(), place_id))

    def delete_place(self, place_id: int) -> None:
        self.execute("DELETE FROM places WHERE id=?", (place_id,))

    # ---- geocode cache ---------------------------------------------------
    def cached_geocode(self, query: str) -> tuple[bool, dict | None]:
        row = self.one("SELECT result FROM geocode_cache WHERE query=?", (query,))
        if row is None:
            return False, None
        return True, json.loads(row["result"]) if row["result"] else None

    def cache_geocode(self, query: str, result: dict | None) -> None:
        self.execute(
            "INSERT OR REPLACE INTO geocode_cache(query, result, created_at) VALUES (?,?,?)",
            (query, json.dumps(result) if result else None, time.time()),
        )
