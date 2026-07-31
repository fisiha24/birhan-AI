"""
Birhan AI
Text-to-Speech Service

Render-safe gTTS implementation.

Features:
- gTTS only
- English + Amharic
- [PAUSE:x] support
- FFmpeg timeout protection
- Temporary-file cleanup
- Compatible with app.py
"""

from pathlib import Path
import base64
import re
import subprocess
import sys


# ============================================================
# SETTINGS
# ============================================================

GTTS_TIMEOUT = 45
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

    text = text.replace("**", "")
    text = text.replace("__", "")
    text = text.replace("#", "")

    text = re.sub(r"\s+", " ", text)

    return text.strip()


# ============================================================
# PAUSE MARKERS
# ============================================================

def _extract_pause_markers(text):
    """
    Extract:

        [PAUSE:3]
        [PAUSE: 2.5]

    Returns:

        cleaned_text, total_pause_seconds
    """

    total_pause = 0.0

    pattern = re.compile(
        r"\[PAUSE:\s*(\d+(?:\.\d+)?)\s*\]",
        re.IGNORECASE,
    )

    def consume(match):
        nonlocal total_pause

        total_pause += float(match.group(1))

        return " "

    cleaned = pattern.sub(
        consume,
        text,
    )

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
    Convert application language to gTTS language code.
    """

    value = str(
        language or ""
    ).strip().lower()

    if value in {
        "am",
        "amh",
        "amharic",
    }:

        return "am"

    return "en"


# ============================================================
# GTTS CHILD PROCESS
# ============================================================

def _generate_gtts_with_timeout(
    text,
    language_code,
    output_path,
):
    """
    Run gTTS inside a child process.

    This prevents a stuck Google TTS request from
    blocking the main Flask process forever.
    """

    output_path = Path(
        output_path
    )

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
            f"gTTS timed out after "
            f"{GTTS_TIMEOUT} seconds."
        ) from error

    except Exception as error:

        raise RuntimeError(
            f"Could not start gTTS: {error}"
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
            "gTTS finished but no audio file was created."
        )

    if output_path.stat().st_size <= 0:

        raise RuntimeError(
            "gTTS created an empty audio file."
        )

    return output_path


# ============================================================
# FFMPEG RUNNER
# ============================================================

def _run_ffmpeg(
    command,
    cwd=None,
    timeout=FFMPEG_TIMEOUT,
):
    """
    Run FFmpeg safely with timeout.
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
            f"FFmpeg timed out after "
            f"{timeout} seconds."
        ) from error

    except FileNotFoundError as error:

        raise RuntimeError(
            "FFmpeg is not installed or "
            "is not available on PATH."
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
# CREATE SILENCE
# ============================================================

def _create_silence(
    duration_seconds,
    output_path,
):
    """
    Create silent MP3 audio.
    """

    duration_seconds = max(
        0.1,
        float(duration_seconds),
    )

    command = [
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

    _run_ffmpeg(
        command,
        cwd=Path(output_path).parent,
    )


# ============================================================
# CONCAT AUDIO
# ============================================================

def _combine_audio(
    speech_path,
    silence_path,
    output_path,
):
    """
    Combine speech and silence.
    """

    speech_path = Path(
        speech_path
    )

    silence_path = Path(
        silence_path
    )

    output_path = Path(
        output_path
    )

    concat_file = (
        output_path.parent
        / f"{output_path.stem}_concat.txt"
    )

    try:

        concat_file.write_text(
            (
                f"file '{speech_path.name}'\n"
                f"file '{silence_path.name}'\n"
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

    finally:

        try:

            if concat_file.exists():
                concat_file.unlink()

        except OSError:
            pass


# ============================================================
# MAIN AUDIO FUNCTION
# ============================================================

def generate_audio(
    text,
    output_path,
    language="English",
    rate="-25%",
    pause_seconds=0.0,
):
    """
    Generate MP3 audio.

    Returns:

        {
            "path": "...",
            "word_boundaries": [],
            "engine": "gtts"
        }

    gTTS does not provide word-level timestamps,
    so word_boundaries is intentionally empty.
    """

    # --------------------------------------------------------
    # CLEAN TEXT
    # --------------------------------------------------------

    text = clean_text(
        text
    )

    text, marker_pause = (
        _extract_pause_markers(
            text
        )
    )

    total_pause = (
        float(pause_seconds or 0)
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

    speech_path = (
        output_path.parent
        / f"{output_path.stem}_speech.mp3"
    )

    silence_path = (
        output_path.parent
        / f"{output_path.stem}_pause.mp3"
    )

    # --------------------------------------------------------
    # REMOVE OLD FILES
    # --------------------------------------------------------

    for path in (
        speech_path,
        silence_path,
        output_path,
    ):

        try:

            if path.exists():
                path.unlink()

        except OSError:
            pass

    have_speech = False
    have_silence = False

    try:

        # ====================================================
        # SPEECH
        # ====================================================

        if text:

            language_code = (
                _get_gtts_language(
                    language
                )
            )

            _generate_gtts_with_timeout(
                text=text,
                language_code=language_code,
                output_path=speech_path,
            )

            have_speech = (
                speech_path.exists()
                and speech_path.stat().st_size > 0
            )

        # ====================================================
        # SILENCE
        # ====================================================

        if total_pause > 0:

            _create_silence(
                duration_seconds=total_pause,
                output_path=silence_path,
            )

            have_silence = (
                silence_path.exists()
                and silence_path.stat().st_size > 0
            )

        # ====================================================
        # SPEECH + SILENCE
        # ====================================================

        if have_speech and have_silence:

            _combine_audio(
                speech_path=speech_path,
                silence_path=silence_path,
                output_path=output_path,
            )

        # ====================================================
        # SPEECH ONLY
        # ====================================================

        elif have_speech:

            speech_path.replace(
                output_path
            )

        # ====================================================
        # SILENCE ONLY
        # ====================================================

        elif have_silence:

            silence_path.replace(
                output_path
            )

        # ====================================================
        # NOTHING
        # ====================================================

        else:

            raise RuntimeError(
                "No audio clip was created."
            )

    finally:

        # ----------------------------------------------------
        # CLEAN TEMP FILES
        # ----------------------------------------------------

        for path in (
            speech_path,
            silence_path,
        ):

            try:

                if path.exists():
                    path.unlink()

            except OSError:
                pass

    # ========================================================
    # FINAL CHECK
    # ========================================================

    if not output_path.exists():

        raise RuntimeError(
            "MP3 audio file was not created."
        )

    if output_path.stat().st_size <= 0:

        raise RuntimeError(
            "MP3 audio file is empty."
        )

    return {
        "path": str(output_path),
        "word_boundaries": [],
        "engine": "gtts",
    }
