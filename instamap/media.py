"""Turning a downloaded video into things we can read: audio speech and on-screen text."""

from __future__ import annotations

import functools
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def ffmpeg_exe() -> str | None:
    """System ffmpeg if there is one, otherwise the copy bundled with imageio-ffmpeg."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def have_ffmpeg() -> bool:
    return ffmpeg_exe() is not None


def extract_audio(video: Path, out: Path) -> Path | None:
    """16 kHz mono WAV, the format Whisper wants. Returns None if the video has no audio."""
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out.exists() or out.stat().st_size < 1024:
        return None
    return out


def extract_frames(video: Path, out_dir: Path, interval: float, max_frames: int) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(video),
        "-vf", f"fps=1/{interval},scale='min(720,iw)':-2",
        "-frames:v", str(max_frames), str(out_dir / "f_%03d.jpg"),
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    return sorted(out_dir.glob("f_*.jpg"))


# --------------------------------------------------------------------------- speech
@functools.lru_cache(maxsize=1)
def _whisper(model_name: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log.warning("faster-whisper not installed; skipping speech. pip install faster-whisper")
        return None
    try:
        # Downloads the model once (from Hugging Face) on first use, then works offline.
        return WhisperModel(model_name, device="auto", compute_type="int8")
    except Exception as exc:
        log.warning("Couldn't load Whisper model %r, skipping speech: %s", model_name, exc)
        return None


def transcribe(audio: Path, model_name: str) -> str:
    model = _whisper(model_name)
    if model is None:
        return ""
    try:
        segments, _info = model.transcribe(str(audio), vad_filter=True, beam_size=1)
        return " ".join(s.text.strip() for s in segments).strip()
    except Exception as exc:
        log.warning("Transcription failed for %s: %s", audio.name, exc)
        return ""


# --------------------------------------------------------------------------- OCR
@functools.lru_cache(maxsize=1)
def _ocr_engine():
    """Prefer RapidOCR (pip only, good on stylised captions); fall back to Tesseract."""
    try:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
        return lambda img: " ".join(line[1] for line in (engine(str(img))[0] or []))
    except ImportError:
        pass
    try:
        import pytesseract
        from PIL import Image
        if shutil.which("tesseract"):
            return lambda img: pytesseract.image_to_string(Image.open(img))
    except ImportError:
        pass
    log.warning("No OCR engine installed; skipping on-screen text. pip install rapidocr-onnxruntime")
    return None


def ocr_images(images: list[Path]) -> str:
    engine = _ocr_engine()
    if engine is None:
        return ""
    seen: set[str] = set()
    lines = []
    for img in images:
        try:
            text = " ".join(engine(img).split())
        except Exception as exc:  # one bad frame shouldn't sink the post
            log.debug("OCR failed on %s: %s", img, exc)
            continue
        # Text overlays tend to stay on screen for several frames; keep each once.
        key = text.lower()
        if len(text) >= 3 and key not in seen:
            seen.add(key)
            lines.append(text)
    return "\n".join(lines)
