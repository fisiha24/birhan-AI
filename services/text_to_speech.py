"""
Birhan AI - Text-to-Speech Service
ቀላል እና አስተማማኝ የgTTS አገልግሎት (ያለ Edge TTS)
"""

from gtts import gTTS
from pathlib import Path
import re
import subprocess

# ============================================================
# CLEAN TEXT
# ============================================================

def clean_text(text):
    """Clean text by removing extra spaces and special markers"""
    if not text:
        return ""
    text = str(text)
    text = re.sub(r"\s+", " ", text)
    text = text.replace("**", "").replace("__", "").replace("#", "")
    return text.strip()

# ============================================================
# PAUSE MARKER
# ============================================================

def _extract_pause_markers(text):
    """
    Remove any "[PAUSE:n]" markers from the text and return
    (clean_text_without_markers, total_pause_seconds).
    """
    total_pause = 0.0
    pattern = re.compile(r"\[PAUSE:\s*(\d+(?:\.\d+)?)\s*\]", re.IGNORECASE)
    
    def _consume(match):
        nonlocal total_pause
        total_pause += float(match.group(1))
        return " "
    
    cleaned = pattern.sub(_consume, text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, total_pause

# ============================================================
# BUILD A SILENT-PAUSE CLIP
# ============================================================

def _build_silence_command(duration_seconds, output_path):
    """Build ffmpeg command to generate a silent audio clip"""
    duration_seconds = max(0.1, float(duration_seconds))
    return [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=24000:cl=mono",
        "-t",
        str(duration_seconds),
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(output_path),
    ]

# ============================================================
# GENERATE AUDIO - gTTS ONLY (NO Edge TTS)
# ============================================================

def generate_audio(
    text,
    output_path,
    language="English",
    rate="-25%",   # Not used for gTTS - kept for compatibility
    pause_seconds=0.0,
):
    """
    Generate speech audio for the given text using Google gTTS.
    
    This version uses ONLY gTTS - NO Edge TTS - to avoid 403 errors.
    
    Args:
        text: The text to convert to speech
        output_path: Where to save the audio file
        language: "English" or "Amharic"
        rate: Not used (kept for compatibility)
        pause_seconds: Seconds of silence to append
    
    Returns:
        dict: {"path": str(output_path), "word_boundaries": []}
    """
    # Clean the text
    text = clean_text(text)
    text, marker_pause_seconds = _extract_pause_markers(text)
    total_pause_seconds = float(pause_seconds) + marker_pause_seconds

    if not text and total_pause_seconds <= 0:
        raise ValueError("Text-to-speech text is empty.")

    # Prepare output paths
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    speech_mp3_path = output_path.parent / f"{output_path.stem}_speech.mp3"
    silence_mp3_path = output_path.parent / f"{output_path.stem}_pause.mp3"

    # ---- Generate speech using gTTS ----
    have_speech_clip = False
    
    if text:
        try:
            # Select language
            lang = 'am' if 'amharic' in language.lower() else 'en'
            
            # Generate speech with gTTS (slow=True for clearer pronunciation)
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

    # ---- Combine speech + silence (if any) into the final file ----
    if have_speech_clip and have_silence_clip:
        # Combine both clips using ffmpeg concat
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
        ]

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
        # Speech only
        speech_mp3_path.replace(output_path)

    elif have_silence_clip:
        # Silence only
        silence_mp3_path.replace(output_path)

    else:
        raise RuntimeError("No audio clip was created.")

    # ---- Clean up temporary files ----
    for temp_file in (speech_mp3_path, silence_mp3_path):
        try:
            if temp_file.exists():
                temp_file.unlink()
        except Exception:
            pass

    if not output_path.exists():
        raise RuntimeError("MP3 audio file was not created.")

    # Return result (word_boundaries is empty because gTTS doesn't provide timing)
    return {
        "path": str(output_path),
        "word_boundaries": [],
    }
