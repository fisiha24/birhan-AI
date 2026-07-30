"""
Birhan AI - Text-to-Speech Service
gTTS with SSL fix for Render
"""

from gtts import gTTS
from pathlib import Path
import re
import subprocess
import ssl
import requests
import urllib3

# SSL warnings ን ዝም አድርግ
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def clean_text(text):
    if not text:
        return ""
    text = str(text)
    text = re.sub(r"\s+", " ", text)
    text = text.replace("**", "").replace("__", "").replace("#", "")
    return text.strip()

def _extract_pause_markers(text):
    total_pause = 0.0
    pattern = re.compile(r"\[PAUSE:\s*(\d+(?:\.\d+)?)\s*\]", re.IGNORECASE)
    def _consume(match):
        nonlocal total_pause
        total_pause += float(match.group(1))
        return " "
    cleaned = pattern.sub(_consume, text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, total_pause

def _build_silence_command(duration_seconds, output_path):
    duration_seconds = max(0.1, float(duration_seconds))
    return [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
        "-t", str(duration_seconds), "-codec:a", "libmp3lame", "-b:a", "128k", str(output_path)
    ]

def generate_audio(text, output_path, language="English", rate="-25%", pause_seconds=0.0):
    text = clean_text(text)
    text, marker_pause = _extract_pause_markers(text)
    total_pause = float(pause_seconds) + marker_pause

    if not text and total_pause <= 0:
        raise ValueError("Text-to-speech text is empty.")

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
        ]
        subprocess.run(cmd, capture_output=True, text=True, cwd=str(output_path.parent))
        concat_file.unlink(missing_ok=True)
    elif have_speech:
        speech_mp3.replace(output_path)
    elif have_silence:
        silence_mp3.replace(output_path)
    else:
        raise RuntimeError("No audio clip was created.")

    for f in [speech_mp3, silence_mp3]:
        try:
            if f.exists(): f.unlink()
        except: pass

    if not output_path.exists():
        raise RuntimeError("MP3 audio file was not created.")

    return {"path": str(output_path), "word_boundaries": [], "engine": "gtts"}