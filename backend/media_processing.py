"""Bounded media decoding before any model sees customer attachments."""
from array import array
import base64
import binascii
from io import BytesIO
from pathlib import Path
import subprocess
import sys
import wave
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_MEDIA_BYTES = 20 * 1024 * 1024
MAX_BASE64_CHARS = 4 * ((MAX_MEDIA_BYTES + 2) // 3) + 256
MAX_IMAGE_PIXELS = 20_000_000
MAX_AUDIO_SECONDS = 600
AUDIO_FORMATS = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg", "opus", "flac"}
AUDIO_MIME_FORMATS = {
    "audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/mp4": "mp4",
    "audio/m4a": "m4a", "audio/x-m4a": "m4a", "audio/wav": "wav",
    "audio/x-wav": "wav", "audio/wave": "wav", "audio/webm": "webm",
    "audio/ogg": "ogg", "application/ogg": "ogg", "audio/opus": "opus",
    "audio/flac": "flac", "audio/x-flac": "flac",
}


class MediaError(ValueError):
    """Stable, non-sensitive reason suitable for logs and API error handling."""


def decode_media_base64(value: str, kind: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > MAX_BASE64_CHARS:
        raise MediaError("media_empty_or_too_large")
    if value.startswith("data:"):
        header, separator, value = value.partition(",")
        mime = header[5:].split(";", 1)[0].lower()
        if not separator or not header.endswith(";base64") or not (
            mime.startswith(kind + "/") or (kind == "audio" and mime == "application/ogg")
        ):
            raise MediaError("media_invalid_data_url")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise MediaError("media_invalid_base64") from None
    if not data or len(data) > MAX_MEDIA_BYTES:
        raise MediaError("media_empty_or_too_large")
    return data


def prepare_image(value: str) -> tuple[str, str]:
    """Verify bytes, fix camera rotation and strip metadata without shrinking text.

    The same normalized pixels go to the scope guard and the support model.
    Animated files are rejected so unseen frames cannot bypass the guard.
    """
    data = decode_media_base64(value, "image")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP", "GIF"}:
                    raise MediaError("image_unsupported_format")
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise MediaError("image_too_many_pixels")
                if getattr(source, "n_frames", 1) != 1:
                    raise MediaError("image_animated")
                source.verify()
            with Image.open(BytesIO(data)) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source).convert("RGBA")
                normalized = Image.new("RGB", oriented.size, "white")
                normalized.paste(oriented, mask=oriented.getchannel("A"))
                output = BytesIO()
                normalized.save(output, format="PNG")
                mime = "image/png"
                if output.tell() > MAX_MEDIA_BYTES:
                    output = BytesIO()
                    normalized.save(output, format="JPEG", quality=95, subsampling=0)
                    mime = "image/jpeg"
                if output.tell() > MAX_MEDIA_BYTES:
                    raise MediaError("image_too_large")
                return base64.b64encode(output.getvalue()).decode("ascii"), mime
    except MediaError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise MediaError("image_invalid") from None


def normalize_audio_format(value: str) -> str:
    value = str(value or "").strip().lower().split(";", 1)[0].lstrip(".")
    return AUDIO_MIME_FORMATS.get(value, {"oga": "ogg", "ptt": "ogg"}.get(value, value))


def detect_audio_format(data: bytes) -> str:
    # HubSpot filenames and MIME hints can disagree with the actual container.
    if data.startswith(b"OggS"):
        return "ogg"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "mp4"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return "webm"
    if data.startswith(b"fLaC"):
        return "flac"
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 255 and data[1] & 0xE0 == 0xE0):
        return "mp3"
    raise MediaError("audio_invalid_container")


def prepare_audio(data: bytes, audio_format: str, folder: str) -> Path:
    """Decode all containers to bounded PCM; reject truncation, corruption and silence."""
    if normalize_audio_format(audio_format) not in AUDIO_FORMATS or not data or len(data) > MAX_MEDIA_BYTES:
        raise MediaError("audio_invalid")
    detected = detect_audio_format(data)
    source = Path(folder) / ("input." + detected)
    output = Path(folder) / "transcription.wav"
    source.write_bytes(data)
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
             "-protocol_whitelist", "file,pipe", "-i", str(source), "-map", "0:a:0",
             "-vn", "-t", str(MAX_AUDIO_SECONDS + 1), "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", "-map_metadata", "-1", str(output)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError:
        raise MediaError("audio_decoder_unavailable") from None
    except (subprocess.TimeoutExpired, OSError):
        raise MediaError("audio_conversion_failed") from None
    if result.returncode != 0 or not output.is_file():
        raise MediaError("audio_conversion_failed")
    try:
        with wave.open(str(output), "rb") as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
                raise MediaError("audio_conversion_failed")
            duration = audio.getnframes() / audio.getframerate()
            if duration > MAX_AUDIO_SECONDS:
                raise MediaError("audio_too_long")
            if duration < 0.15:
                raise MediaError("audio_empty")
            audible = False
            while frames := audio.readframes(16000):
                samples = array("h", frames)
                if sys.byteorder != "little":
                    samples.byteswap()
                if any(abs(sample) > 8 for sample in samples):
                    audible = True
            if not audible:
                raise MediaError("audio_silent")
    except (wave.Error, EOFError, OSError):
        raise MediaError("audio_conversion_failed") from None
    if output.stat().st_size > MAX_MEDIA_BYTES:
        raise MediaError("audio_too_large")
    return output
