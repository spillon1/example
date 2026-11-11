"""Settings, read from environment variables with sensible defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("INSTAMAP_DATA_DIR", str(Path.home() / ".instamap"))))
    host: str = field(default_factory=lambda: _env("INSTAMAP_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("INSTAMAP_PORT", "8765")))

    # Speech to text (faster-whisper). "tiny", "base", "small", "medium".
    whisper_model: str = field(default_factory=lambda: _env("INSTAMAP_WHISPER_MODEL", "small"))
    # Seconds between frames sampled for on-screen text.
    frame_interval: float = field(default_factory=lambda: float(_env("INSTAMAP_FRAME_INTERVAL", "1.5")))
    max_frames: int = field(default_factory=lambda: int(_env("INSTAMAP_MAX_FRAMES", "40")))

    # Optional local LLM through Ollama (free, runs on your machine).
    ollama_url: str = field(default_factory=lambda: _env("INSTAMAP_OLLAMA_URL", "http://127.0.0.1:11434"))
    ollama_model: str = field(default_factory=lambda: _env("INSTAMAP_OLLAMA_MODEL", "qwen2.5:7b"))

    # Pause between Instagram requests, to stay well under rate limits.
    ig_delay: float = field(default_factory=lambda: float(_env("INSTAMAP_IG_DELAY", "3")))
    # Delete downloaded videos once they've been analysed.
    keep_media: bool = field(default_factory=lambda: _env("INSTAMAP_KEEP_MEDIA", "0") == "1")

    geocoder_user_agent: str = field(default_factory=lambda: _env("INSTAMAP_GEOCODER_UA", "instamap-local/0.1"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "instamap.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def browser_profile(self) -> Path:
        return self.data_dir / "browser-profile"

    @property
    def session_file(self) -> Path:
        return self.data_dir / "instagram-session.json"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
