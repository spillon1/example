"""Analyse one saved post: download it, read it, find places, put them on the map."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from . import media as media_tools
from .config import Settings
from .db import Database
from .geocode import Geocoder
from .places import find_places

log = logging.getLogger(__name__)

MAX_ITEMS_PER_POST = 10


def analyse_media(post: dict, downloader, settings: Settings) -> tuple[str, str]:
    """Download each photo/video in the post and return (transcript, on-screen text)."""
    workdir = settings.media_dir / str(post["media_id"])
    workdir.mkdir(parents=True, exist_ok=True)
    transcripts, ocr_parts = [], []
    try:
        for i, item in enumerate(json.loads(post.get("media") or "[]")[:MAX_ITEMS_PER_POST]):
            ext = "mp4" if item["kind"] == "video" else "jpg"
            path = downloader(item["url"], workdir / f"{i}.{ext}")
            if item["kind"] == "video":
                if not media_tools.have_ffmpeg():
                    raise RuntimeError("ffmpeg is not installed; it's needed to read videos")
                audio = media_tools.extract_audio(path, workdir / f"{i}.wav")
                if audio:
                    transcripts.append(media_tools.transcribe(audio, settings.whisper_model))
                frames = media_tools.extract_frames(path, workdir / f"{i}_frames",
                                                    settings.frame_interval, settings.max_frames)
                ocr_parts.append(media_tools.ocr_images(frames))
            else:
                ocr_parts.append(media_tools.ocr_images([path]))
    finally:
        if not settings.keep_media:
            shutil.rmtree(workdir, ignore_errors=True)
    return "\n".join(t for t in transcripts if t), "\n".join(o for o in ocr_parts if o)


def process_post(post: dict, *, db: Database, geocoder: Geocoder, downloader, settings: Settings) -> int:
    """Returns the number of places recorded for this post."""
    tag = json.loads(post["ig_location"]) if post.get("ig_location") else None
    found = 0

    # 1. Instagram's own location sticker/tag: already has coordinates, most reliable.
    if tag:
        geo = {"lat": tag.get("lat"), "lon": tag.get("lon"), "address": tag.get("address"), "city": tag.get("city")}
        if geo["lat"] is None:
            geo, _, _ = geocoder.locate(tag["name"], tag.get("city"))
        db.add_place(post_id=post["id"], name=tag["name"], method="ig_location",
                     evidence="Instagram location tag", confidence=0.95, geo=geo, query=tag["name"])
        found += 1

    # 2. Everything said, written or shown in the post.
    transcript, ocr = analyse_media(post, downloader, settings)
    candidates = find_places(
        caption=post.get("caption") or "", transcript=transcript, ocr=ocr, username=post.get("username"),
        ig_tag=tag, ollama_url=settings.ollama_url, ollama_model=settings.ollama_model,
    )

    for c in candidates:
        if tag and c.name.lower() == tag["name"].lower():
            continue
        geo, query, factor = geocoder.locate(c.name, c.area)
        if not geo and c.confidence < 0.5:
            continue  # a weak guess that isn't on any map is almost always noise
        confidence = round(c.confidence * (factor or 0.5), 2)
        category = "area" if c.kind == "area" and not c.category else c.category
        if geo and not category and geo.get("type") in ("city", "town", "village", "country",
                                                           "state", "administrative", "region"):
            category = "area"
        db.add_place(post_id=post["id"], name=c.name, method=c.method, evidence=c.evidence,
                     confidence=confidence, geo=geo, query=query, category=category)
        found += 1

    db.finish_post(post["id"], transcript=transcript, ocr_text=ocr)
    return found
