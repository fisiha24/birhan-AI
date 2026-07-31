"""
Birhan AI - Text-to-Speech Service

Reliable gTTS-based TTS service for Render deployment.

Features:
- gTTS only
- Amharic and English support
- [PAUSE:x] markers
- Explicit subprocess timeouts
- Safe FFmpeg execution
- Cleanup of temporary files
- Compatible with app.py
"""

from pathlib import Path
import base64
import re
import subprocess
import sys
import tempfile

from gtts import gTTS


# ============================================================
# TIMEOUT SETTINGS
# ============================================================

# Maximum time allowed for one gTTS network operation.
GTTS_TIMEOUT = 45

# Maximum time allowed for FFmpeg operations.
FFMPEG_TIMEOUT = 30


# ============================================================
# TEXT CLEANING
# ============================================================

def clean_text(text):
    """
    Clean text before sending it to gTTS.
    """

    if not text:
        return ""

    text = str(text)

    # Remove excessive whitespace.
    text = re.sub(r"\s+", " ", text)

    # Remove simple Markdown formatting.
    text = text.replace("**", "")
    text = text.replace("__", "")
    text = text.replace("#", "")

    return text.strip()


# ============================================================
# PAUSE MARKERS
# ============================================================

def _extract_pause_markers(text):
    """
    Extract markers such as:

        [PAUSE:3]
        [PAUSE: 2.5]

    and return:

        cleaned_text, total_pause_seconds
    """

    total_pause = 0.0

    pattern = re.compile(
        r"\[PAUSE:\s*(\d+(?:\.\d+)?)\s*\]",
        re.IGNORECASE,
    )

    def _consume(match):
        nonlocal total_pause

        total_pause += float(match.group(1))

        return " "

    cleaned = pattern.sub(_consume, text)

    cleaned = re.sub(
        r"\s+",
        " ",
        cleaned,
    ).strip()

    return cleaned, total_pause


# ============================================================
# LANGUAGE
# ============================================================

def _get_gtts_language(language):
    """
    Convert the application's language name to a gTTS language code.
    """

    language_text = str(language or "").strip().lower()

    if (
        "amharic" in language_text
        or language_text in {"am", "amh"}
    ):
        return "am"

    return "en"


# ============================================================
# SAFE GTTS GENERATION
# ============================================================

def _generate_gtts_with_timeout(
    text,
    language_code,
    output_path,
):
    """
    Run gTTS in a separate Python process.

    Why?

    gTTS performs a network request internally and does not expose
    a convenient timeout parameter through gTTS.save().

    Running it in a child process lets us terminate it if the
    Render network request becomes stuck.
    """

    output_path = Path(output_path)

    # Base64 avoids quoting problems when passing large text
    # through a Python -c command.
    encoded_text = base64.b64encode(
        text.encode("utf-8")
    ).decode("ascii")

    child_code = r"""
import base64
import sys

from gtts import gTTS

encoded_text = sys.argv[1]
language = sys.argv[2]
output_file = sys.argv[3]

text = base64.b64decode(
    encoded_text.encode("ascii")
).decode("utf-8")

tts = gTTS(
    text=text,
    lang=language,
    slow=True,
)

tts.save(output_file)
"""

    command = [
        sys.executable,
        "-c",
        child_code,
        encoded_text,
        language_code,
        str(output_path),
    ]

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=GTTS_TIMEOUT,
        )

    except subprocess.TimeoutExpired as error:

        raise RuntimeError(
            f"gTTS timed out after {GTTS_TIMEOUT} seconds."
        ) from error

    except Exception as error:

        raise RuntimeError(
            f"Could not start gTTS process: {error}"
        ) from error

    if result.returncode != 0:

        stderr = (
            result.stderr.strip()
            if result.stderr
            else "Unknown gTTS error."
        )

        raise RuntimeError(
            f"gTTS process failed: {stderr}"
        )

    if not output_path.exists():

        raise RuntimeError(
            "gTTS finished but did not create the audio file."
        )

    if output_path.stat().st_size <= 0:

        raise RuntimeError(
            "gTTS created an empty audio file."
        )

    return output_path


# ============================================================
# FFMPEG SILENCE
# ============================================================

def _build_silence_command(
    duration_seconds,
    output_path,
):
    """
    Build an FFmpeg command that creates MP3 silence.
    """

    duration_seconds = max(
        0.1,
        float(duration_seconds),
    )

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
# SAFE FFMPEG RUNNER
# ============================================================

def _run_ffmpeg(
    command,
    cwd=None,
    timeout=FFMPEG_TIMEOUT,
):
    """
    Run FFmpeg with a hard timeout.
    """

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
        )

    except subprocess.TimeoutExpired as error:

        raise RuntimeError(
            f"FFmpeg timed out after {timeout} seconds."
        ) from error

    except FileNotFoundError as error:

        raise RuntimeError(
            "FFmpeg is not installed or is not available "
            "on the Render PATH."
        ) from error

    if result.returncode != 0:

        stderr = (
            result.stderr.strip()
            if result.stderr
            else "Unknown FFmpeg error."
        )

        raise RuntimeError(
            f"FFmpeg failed: {stderr}"
        )

    return result


# ============================================================
# AUDIO GENERATION
# ============================================================

def generate_audio(
    text,
    output_path,
    language="English",
    rate="-25%",
    pause_seconds=0.0,
):
    """
    Generate one MP3 audio file.

    Returns:

        {
            "path": "...",
            "word_boundaries": [],
            "engine": "gtts"
        }

    `word_boundaries` remains empty because gTTS does not provide
    word-level speech timing information.
    """

    # --------------------------------------------------------
    # CLEAN INPUT
    # --------------------------------------------------------

    text = clean_text(text)

    text, marker_pause = _extract_pause_markers(
        text
    )

    total_pause = (
        float(pause_seconds or 0.0)
        + marker_pause
    )

    if not text and total_pause <= 0:

        raise ValueError(
            "Text-to-speech text is empty."
        )

    # --------------------------------------------------------
    # PATHS
    # --------------------------------------------------------

    output_path = Path(
        output_path
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    speech_mp3 = (
        output_path.parent
        / f"{output_path.stem}_speech.mp3"
    )

    silence_mp3 = (
        output_path.parent
        / f"{output_path.stem}_pause.mp3"
    )

    concat_file = (
        output_path.parent
        / f"{output_path.stem}_concat.txt"
    )

    # --------------------------------------------------------
    # REMOVE OLD TEMPORARY FILES
    # --------------------------------------------------------

    for file_path in (
        speech_mp3,
        silence_mp3,
        concat_file,
        output_path,
    ):

        try:

            if file_path.exists():
                file_path.unlink()

        except OSError:
            pass

    have_speech = False
    have_silence = False

    try:

        # ====================================================
        # 1. GENERATE SPEECH
        # ====================================================

        if text:

            lang = _get_gtts_language(
                language
            )

            try:

                _generate_gtts_with_timeout(
                    text=text,
                    language_code=lang,
                    output_path=speech_mp3,
                )

                have_speech = (
                    speech_mp3.exists()
                    and speech_mp3.stat().st_size > 0
                )

            except Exception as error:

                raise RuntimeError(
                    f"gTTS failed: {error}"
                ) from error

        # ====================================================
        # 2. CREATE PAUSE AUDIO
        # ====================================================

        if total_pause > 0:

            command = _build_silence_command(
                total_pause,
                silence_mp3,
            )

            _run_ffmpeg(
                command,
                cwd=output_path.parent,
            )

            have_silence = (
                silence_mp3.exists()
                and silence_mp3.stat().st_size > 0
            )

        # ====================================================
        # 3. COMBINE SPEECH + PAUSE
        # ====================================================

        if have_speech and have_silence:

            # Use only the file names because FFmpeg runs
            # inside output_path.parent.
            concat_file.write_text(
                (
                    f"file '{speech_mp3.name}'\n"
                    f"file '{silence_mp3.name}'\n"
                ),
                encoding="utf-8",
            )

            command = [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_file.name,
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "128k",
                output_path.name,
            ]

            _run_ffmpeg(
                command,
                cwd=output_path.parent,
            )

        # ====================================================
        # 4. SPEECH ONLY
        # ====================================================

        elif have_speech:

            speech_mp3.replace(
                output_path
            )

        # ====================================================
        # 5. SILENCE ONLY
        # ====================================================

        elif have_silence:

            silence_mp3.replace(
                output_path
            )

        # ====================================================
        # 6. NOTHING CREATED
        # ====================================================

        else:

            raise RuntimeError(
                "No audio clip was created."
            )

    finally:

        # ----------------------------------------------------
        # ALWAYS CLEAN TEMP FILES
        # ----------------------------------------------------

        for file_path in (
            speech_mp3,
            silence_mp3,
            concat_file,
        ):

            try:

                if file_path.exists():
                    file_path.unlink()

            except OSError:
                pass

    # ========================================================
    # FINAL VALIDATION
    # ========================================================

    if not output_path.exists():

        raise RuntimeError(
            "MP3 audio file was not created."
        )

    if output_path.stat().st_size <= 0:

        raise RuntimeError(
            "MP3 audio file was created but is empty."
        )

    return {
        "path": str(output_path),
        "word_boundaries": [],
        "engine": "gtts",
    }
