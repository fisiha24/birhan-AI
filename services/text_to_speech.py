"""
Birhan AI - Text-to-Speech Service
<<<<<<< HEAD
gTTS with SSL fix for Render
=======
ያለ edge-tts - gTTS ብቻ (የ403 ስህተት የለም)
>>>>>>> 04312feba63cb9bd7ed381b63ee81a92c0aef007
"""

from gtts import gTTS
from pathlib import Path
import re
import subprocess
<<<<<<< HEAD
import ssl
import requests
import urllib3

# SSL warnings ን ዝም አድርግ
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
=======

# ============================================================
# CLEAN TEXT
# ============================================================
>>>>>>> 04312feba63cb9bd7ed381b63ee81a92c0aef007

def clean_text(text):
    if not text:
        return ""
    text = str(text)
    text = re.sub(r"\s+", " ", text)
    text = text.replace("**", "").replace("__", "").replace("#", "")
    return text.strip()

<<<<<<< HEAD
def _extract_pause_markers(text):
    total_pause = 0.0
    pattern = re.compile(r"\[PAUSE:\s*(\d+(?:\.\d+)?)\s*\]", re.IGNORECASE)
=======
# ============================================================
# PAUSE MARKER
# ============================================================

PAUSE_MARKER_PATTERN = re.compile(
    r"\[PAUSE:\s*(\d+(?:\.\d+)?)\s*\]",
    re.IGNORECASE,
)

def _extract_pause_markers(text):
    total_pause = 0.0
>>>>>>> 04312feba63cb9bd7ed381b63ee81a92c0aef007
    def _consume(match):
        nonlocal total_pause
        total_pause += float(match.group(1))
        return " "
<<<<<<< HEAD
    cleaned = pattern.sub(_consume, text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, total_pause

=======
    cleaned = PAUSE_MARKER_PATTERN.sub(_consume, text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, total_pause

# ============================================================
# BUILD SILENCE CLIP (ffmpeg)
# ============================================================

>>>>>>> 04312feba63cb9bd7ed381b63ee81a92c0aef007
def _build_silence_command(duration_seconds, output_path):
    duration_seconds = max(0.1, float(duration_seconds))
    return [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
        "-t", str(duration_seconds), "-codec:a", "libmp3lame", "-b:a", "128k", str(output_path)
    ]

<<<<<<< HEAD
def generate_audio(text, output_path, language="English", rate="-25%", pause_seconds=0.0):
=======
# ============================================================
# LANGUAGE MAP FOR gTTS
# ============================================================

LANGUAGE_GTTS_MAP = {
    "english": "en",
    "amharic": "am",
}

DEFAULT_GTTS_LANGUAGE = "en"

def _select_gtts_language(language):
    key = str(language or "").strip().lower()
    return LANGUAGE_GTTS_MAP.get(key, DEFAULT_GTTS_LANGUAGE)

# ============================================================
# GENERATE AUDIO - gTTS ONLY
# ============================================================

def generate_audio(
    text,
    output_path,
    language="English",
    rate="-25%",
    pause_seconds=0.0,
):
    """
    Generate speech audio using Google gTTS.
    
    Returns:
        dict: {
            "path": str(output_path),
            "word_boundaries": [],  # gTTS doesn't provide timing
            "engine": "gtts"
        }
    """
    # Clean text
>>>>>>> 04312feba63cb9bd7ed381b63ee81a92c0aef007
    text = clean_text(text)
    text, marker_pause = _extract_pause_markers(text)
    total_pause = float(pause_seconds) + marker_pause

    if not text and total_pause <= 0:
        raise ValueError("Text-to-speech text is empty.")

<<<<<<< HEAD
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    speech_mp3 = output_path.parent / f"{output_path.stem}_speech.mp3"
    silence_mp3 = output_path.parent / f"{output_path.stem}_pause.mp3"

    have_speech = False
    if text:
        try:
            lang = 'am' if 'amharic' in language.lower() else 'en'
            
            # SSL verification ን አሰናክል (Render ላይ ለማስተናገድ)
            session = requests.Session()
            session.verify = False
            
            tts = gTTS(text=text, lang=lang, slow=True)
            tts.save(str(speech_mp3_path))
            have_speech = speech_mp3_path.exists()
        except Exception as e:
            print(f"gTTS failed: {e}")
            # ይህን ለመፍታት አማራጭ ዘዴ
            try:
                import os
                os.system(f'echo "{text}" | festival --tts --language amharic')
            except:
                raise RuntimeError(f"gTTS and fallback both failed: {e}")

    have_silence = False
    if total_pause > 0:
        cmd = _build_silence_command(total_pause, silence_mp3)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            have_silence = silence_mp3.exists()

    if have_speech and have_silence:
        concat_file = output_path.parent / f"{output_path.stem}_concat.txt"
        concat_file.write_text(
            f"file '{speech_mp3.name}'\nfile '{silence_mp3.name}'\n",
            encoding="utf-8"
        )
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_file), "-codec:a", "libmp3lame",
            "-b:a", "128k", str(output_path)
=======
    # Prepare output paths
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    speech_mp3_path = output_path.parent / f"{output_path.stem}_speech.mp3"
    silence_mp3_path = output_path.parent / f"{output_path.stem}_pause.mp3"

    # ---- Generate speech using gTTS ----
    have_speech_clip = False
    
    if text:
        try:
            lang = _select_gtts_language(language)
            tts = gTTS(text=text, lang=lang, slow=True)
            tts.save(str(speech_mp3_path))
            have_speech_clip = speech_mp3_path.exists()
        except Exception as e:
            raise RuntimeError(f"gTTS speech generation failed: {e}")

    # ---- Generate silence pause if needed ----
    have_silence_clip = False
    
    if total_pause_seconds > 0:
        silence_command = _build_silence_command(total_pause_seconds, silence_mp3_path)
        silence_result = subprocess.run(
            silence_command,
            capture_output=True,
            text=True,
        )
        if silence_result.returncode == 0:
            have_silence_clip = silence_mp3_path.exists()
        else:
            print(f"Warning: Silence generation failed: {silence_result.stderr}")

    # ---- Combine speech + silence ----
    if have_speech_clip and have_silence_clip:
        concat_list_path = output_path.parent / f"{output_path.stem}_concat.txt"
        concat_list_path.write_text(
            f"file '{speech_mp3_path.name}'\nfile '{silence_mp3_path.name}'\n",
            encoding="utf-8",
        )

        ffmpeg_command = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list_path),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(output_path),
>>>>>>> 04312feba63cb9bd7ed381b63ee81a92c0aef007
        ]
        subprocess.run(cmd, capture_output=True, text=True, cwd=str(output_path.parent))
        concat_file.unlink(missing_ok=True)
    elif have_speech:
        speech_mp3.replace(output_path)
    elif have_silence:
        silence_mp3.replace(output_path)
    else:
        raise RuntimeError("No audio clip was created.")

<<<<<<< HEAD
    for f in [speech_mp3, silence_mp3]:
=======
        ffmpeg_result = subprocess.run(
            ffmpeg_command,
            capture_output=True,
            text=True,
            cwd=str(output_path.parent),
        )

        if ffmpeg_result.returncode != 0:
            raise RuntimeError(
                "FFmpeg audio concatenation failed:\n"
                + ffmpeg_result.stderr
            )

        concat_list_path.unlink(missing_ok=True)

    elif have_speech_clip:
        speech_mp3_path.replace(output_path)

    elif have_silence_clip:
        silence_mp3_path.replace(output_path)

    else:
        raise RuntimeError("No audio clip was created.")

    # ---- Clean up temporary files ----
    for temp_file in (speech_mp3_path, silence_mp3_path):
>>>>>>> 04312feba63cb9bd7ed381b63ee81a92c0aef007
        try:
            if f.exists(): f.unlink()
        except: pass

    if not output_path.exists():
        raise RuntimeError("MP3 audio file was not created.")

<<<<<<< HEAD
    return {"path": str(output_path), "word_boundaries": [], "engine": "gtts"}
=======
    return {
        "path": str(output_path),
        "word_boundaries": [],
        "engine": "gtts",
    }
>>>>>>> 04312feba63cb9bd7ed381b63ee81a92c0aef007
