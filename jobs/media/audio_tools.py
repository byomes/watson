"""jobs/media/audio_tools.py — Audio metadata, format conversion, and trimming."""
import logging
import re
import os

log = logging.getLogger(__name__)

_PATH_RE = re.compile(r'[\w/~.\-]+\.(?:mp3|m4a|wav|flac|ogg|aac)', re.IGNORECASE)


def get_metadata(path: str) -> dict:
    import mutagen
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    result = {"title": "", "artist": "", "duration": 0, "bitrate": 0}
    try:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".mp3":
            audio = MP3(path)
            result["duration"] = round(audio.info.length)
            result["bitrate"] = audio.info.bitrate // 1000
            tags = audio.tags or {}
            result["title"] = str(tags.get("TIT2", "")).strip()
            result["artist"] = str(tags.get("TPE1", "")).strip()
        elif ext in (".m4a", ".mp4"):
            audio = MP4(path)
            result["duration"] = round(audio.info.length)
            result["bitrate"] = audio.info.bitrate // 1000
            result["title"] = str(audio.tags.get("\xa9nam", [""])[0]).strip()
            result["artist"] = str(audio.tags.get("\xa9ART", [""])[0]).strip()
        else:
            f = mutagen.File(path)
            if f:
                result["duration"] = round(f.info.length)
    except Exception as exc:
        log.error("get_metadata failed: %s", exc)
    return result


def set_metadata(path: str, title: str, artist: str = "Dr. Bill Yomes") -> bool:
    from mutagen.mp3 import MP3
    from mutagen.id3 import TIT2, TPE1
    try:
        audio = MP3(path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags["TIT2"] = TIT2(encoding=3, text=title)
        audio.tags["TPE1"] = TPE1(encoding=3, text=artist)
        audio.save()
        return True
    except Exception as exc:
        log.error("set_metadata failed: %s", exc)
        return False


def convert_audio(input_path: str, output_path: str, format: str = "mp3") -> bool:
    from pydub import AudioSegment
    try:
        audio = AudioSegment.from_file(input_path)
        audio.export(output_path, format=format)
        return True
    except Exception as exc:
        log.error("convert_audio failed: %s", exc)
        return False


def trim_audio(path: str, start_ms: int, end_ms: int, output_path: str) -> bool:
    from pydub import AudioSegment
    try:
        audio = AudioSegment.from_file(path)
        trimmed = audio[start_ms:end_ms]
        fmt = os.path.splitext(output_path)[1].lstrip(".") or "mp3"
        trimmed.export(output_path, format=fmt)
        return True
    except Exception as exc:
        log.error("trim_audio failed: %s", exc)
        return False


def run(message: str = None) -> str:
    if not message:
        return "Audio tools ready."
    match = _PATH_RE.search(message)
    if not match:
        return "Audio tools ready. Provide an audio file path."
    path = match.group(0).replace("~", os.path.expanduser("~"))
    meta = get_metadata(path)
    mins, secs = divmod(meta["duration"], 60)
    duration_str = f"{mins}:{secs:02d}"
    return (
        f"Audio: {os.path.basename(path)}\n"
        f"Title:    {meta['title'] or '—'}\n"
        f"Artist:   {meta['artist'] or '—'}\n"
        f"Duration: {duration_str}\n"
        f"Bitrate:  {meta['bitrate']} kbps"
    )
