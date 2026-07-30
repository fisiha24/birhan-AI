"""
Birhan AI
Text-to-Speech Service

AZURE SPEECH SDK VERSION
(official Microsoft Azure Cognitive Services Speech SDK -
replaces the unofficial `edge-tts` client)

Each scene receives its own audio file.

============================================================
WHY THIS VERSION EXISTS
============================================================

The previous version used `edge-tts`, an unofficial Python
wrapper around the internal endpoint that powers Microsoft
Edge's "Read Aloud" browser feature
(speech.platform.bing.com). That endpoint is not a public
API - it is reverse-engineered from the browser feature - and
Microsoft has been increasingly blocking requests that come
from datacenter / cloud IP ranges (Render, AWS, Heroku, GCP,
etc.) rather than a real end-user's machine. That is what was
causing the

    403 ... TrustedClientToken ...

error once deployed to Render, even though it may have worked
fine on a local machine.

This version instead uses the official, supported Azure
Cognitive Services Speech SDK. It requires a real Azure
Speech resource (key + region), but it is a stable, documented
API that is meant to be called from a server, and will not be
silently blocked.

============================================================
WHAT STAYED THE SAME
============================================================

1. One consistent neural voice per language, spoken in a
   SINGLE synthesis call per scene - no per-sentence tone
   switching.
2. Consecutive duplicate sentences are still de-duplicated
   before synthesis.
3. Real silent pauses (`pause_seconds` and/or a "[PAUSE:n]"
   marker inside the text) are still supported, appended
   with ffmpeg after the spoken audio.
4. `generate_audio()` still returns real per-word timing as
   `word_boundaries`, which services/video_generator.py uses
   to align every board word to the instant it is actually
   spoken - now sourced from the Azure SDK's
   `synthesis_word_boundary` event instead of edge-tts's
   WordBoundary chunks.

============================================================
REQUIREMENTS
============================================================

pip install azure-cognitiveservices-speech --break-system-packages

`ffmpeg` must be available on PATH (already required by the
rest of the app for video/audio work).

============================================================
ENVIRONMENT VARIABLES (set these in Render's dashboard, and
locally in a .env file that is NOT committed to git)
============================================================

AZURE_SPEECH_KEY     - your Azure Speech resource key
AZURE_SPEECH_REGION  - e.g. "eastus", "westeurope", etc.
"""

import os
import re

from pathlib import Path

import subprocess

import azure.cognitiveservices.speech as speechsdk


# ============================================================
# CLEAN TEXT
# ============================================================

def clean_text(text):

    if not text:

        return ""

    text = str(
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    text = text.replace(
        "**",
        "",
    )

    text = text.replace(
        "__",
        "",
    )

    text = text.replace(
        "#",
        "",
    )

    return text.strip()


# ============================================================
# VOICE SELECTION
#
# One consistent, natural-sounding neural voice per language -
# used for the ENTIRE lesson, so the teacher's voice never
# suddenly changes tone or character partway through. Both of
# these voice names also exist as official Azure neural
# voices, so no other change was needed here.
# ============================================================

LANGUAGE_VOICE_MAP = {

    "english": "en-US-GuyNeural",

    "amharic": "am-ET-AmehaNeural",

}

LANGUAGE_LOCALE_MAP = {

    "english": "en-US",

    "amharic": "am-ET",

}

DEFAULT_VOICE = "en-US-GuyNeural"

DEFAULT_LOCALE = "en-US"


def _select_voice(language):

    key = str(language or "").strip().lower()

    return LANGUAGE_VOICE_MAP.get(
        key,
        DEFAULT_VOICE,
    )


def _select_locale(language):

    key = str(language or "").strip().lower()

    return LANGUAGE_LOCALE_MAP.get(
        key,
        DEFAULT_LOCALE,
    )


# ============================================================
# RATE MAPPING
#
# The Azure SDK takes speaking rate as an SSML <prosody
# rate="..."> attribute, which accepts the same "+n%"/"-n%"
# style strings the rest of this app already uses - so the
# value is passed straight through, same as before.
#
# DEFAULT_RATE stays "-25%" so the teacher speaks at a
# clearly slower, more deliberate classroom pace by default.
# ============================================================

DEFAULT_RATE = "-25%"


def _normalize_rate(rate):

    rate = str(rate or DEFAULT_RATE).strip()

    if not rate.startswith(("+", "-")):

        rate = f"+{rate}"

    if not rate.endswith("%"):

        rate = f"{rate}%"

    return rate


# ============================================================
# PAUSE MARKER
#
# A scene's text can request a silent pause (e.g. time for
# students to applaud) by including "[PAUSE:n]" where n is
# the number of seconds of silence, or by passing
# `pause_seconds` directly to generate_audio(). Both are
# supported so callers can choose whichever is convenient.
# ============================================================

PAUSE_MARKER_PATTERN = re.compile(
    r"\[PAUSE:\s*(\d+(?:\.\d+)?)\s*\]",
    re.IGNORECASE,
)


def _extract_pause_markers(text):

    """
    Remove any "[PAUSE:n]" markers from the text and return
    (clean_text_without_markers, total_pause_seconds).
    """

    total_pause = 0.0

    def _consume(match):

        nonlocal total_pause

        total_pause += float(
            match.group(1)
        )

        return " "

    cleaned = PAUSE_MARKER_PATTERN.sub(
        _consume,
        text,
    )

    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned, total_pause


# ============================================================
# SPLIT NARRATION INTO SENTENCES (for de-duplication only)
# ============================================================

def split_into_sentences(text):

    text = clean_text(text)

    if not text:

        return []

    raw_parts = re.split(
        r"(?<=[.!?])\s+",
        text,
    )

    sentences = [
        part.strip()
        for part in raw_parts
        if part.strip()
    ]

    # --------------------------------------------------------
    # Drop consecutive duplicate sentences so the same line
    # is never spoken twice back to back (e.g. an intro
    # phrase or review prompt that was accidentally
    # duplicated upstream).
    # --------------------------------------------------------

    deduped = []

    previous_normalized = None

    for sentence in sentences:

        normalized = re.sub(
            r"\s+",
            " ",
            sentence.strip().lower(),
        )

        if normalized == previous_normalized:

            continue

        deduped.append(sentence)

        previous_normalized = normalized

    return deduped


# ============================================================
# BUILD A SILENT-PAUSE CLIP
#
# Generated at 24000 Hz mono to match the Azure SDK's output
# sample rate configured below, so the two segments join
# cleanly with no audible seam when concatenated.
# ============================================================

def _build_silence_command(duration_seconds, output_path):

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
# SSML BUILDING
#
# The Azure SDK is driven via SSML rather than plain text +
# separate voice/rate arguments, so the voice name, locale,
# and prosody rate are all embedded directly in the markup
# sent to the synthesizer.
# ============================================================

def _escape_ssml_text(text):

    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _build_ssml(text, voice, locale, rate):

    escaped = _escape_ssml_text(text)

    return (
        f'<speak version="1.0" '
        f'xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xml:lang="{locale}">'
        f'<voice name="{voice}">'
        f'<prosody rate="{rate}">{escaped}</prosody>'
        f"</voice>"
        f"</speak>"
    )


# ============================================================
# AZURE SPEECH CONFIG
#
# Reads credentials from environment variables ONLY - never
# hardcode a key here. Set AZURE_SPEECH_KEY and
# AZURE_SPEECH_REGION in Render's dashboard (Environment tab)
# and in a local .env file that is NOT committed to git.
# ============================================================

def _build_speech_config():

    speech_key = os.environ.get("AZURE_SPEECH_KEY")

    speech_region = os.environ.get("AZURE_SPEECH_REGION")

    if not speech_key or not speech_region:

        raise RuntimeError(
            "AZURE_SPEECH_KEY and AZURE_SPEECH_REGION "
            "environment variables must both be set."
        )

    speech_config = speechsdk.SpeechConfig(
        subscription=speech_key,
        region=speech_region,
    )

    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz96KBitRateMonoMp3
    )

    return speech_config


# ============================================================
# RUN AZURE SYNTHESIS - WITH REAL WORD TIMING
#
# Uses the Azure SDK's `synthesis_word_boundary` event to
# capture the REAL offset/duration of every word inside the
# synthesized audio, in the same 100-nanosecond-tick units
# edge-tts used - so services/video_generator.py needs no
# changes to how it consumes `word_boundaries`.
# ============================================================

def _run_synthesis_with_boundaries(text, voice, locale, rate, output_path):

    speech_config = _build_speech_config()

    audio_config = speechsdk.audio.AudioOutputConfig(
        filename=str(output_path)
    )

    synthesizer = speechsdk.SpeechSynthesizer(
        speech_config=speech_config,
        audio_config=audio_config,
    )

    word_boundaries = []

    def _on_word_boundary(evt):

        if evt.boundary_type != speechsdk.SpeechSynthesisBoundaryType.Word:

            return

        start_seconds = float(evt.audio_offset) / 10_000_000.0

        duration_seconds = (
            evt.duration.total_seconds()
            if evt.duration
            else 0.0
        )

        word_boundaries.append(
            {
                "text": evt.text,
                "start": start_seconds,
                "end": start_seconds + duration_seconds,
            }
        )

    synthesizer.synthesis_word_boundary.connect(
        _on_word_boundary
    )

    ssml = _build_ssml(
        text,
        voice,
        locale,
        rate,
    )

    result = synthesizer.speak_ssml_async(ssml).get()

    if result.reason == speechsdk.ResultReason.Canceled:

        cancellation = result.cancellation_details

        error_detail = (
            cancellation.error_details
            if cancellation
            else "unknown error"
        )

        raise RuntimeError(
            f"Azure speech synthesis was canceled: {error_detail}"
        )

    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:

        raise RuntimeError(
            f"Azure speech synthesis failed: {result.reason}"
        )

    return word_boundaries


# ============================================================
# GENERATE AUDIO
# ============================================================

def generate_audio(
    text,
    output_path,
    language="English",
    rate=DEFAULT_RATE,
    pause_seconds=0.0,
):

    """
    pause_seconds: extra seconds of silence appended AFTER
    the spoken text (e.g. time for students to applaud after
    hearing the correct answer). A "[PAUSE:n]" marker inside
    `text` adds to this the same way.

    Returns a dict:
      {
        "path": str(output_path),
        "word_boundaries": [
            {"text": "...", "start": 0.12, "end": 0.34},
            ...
        ],
      }

    `word_boundaries` gives the REAL timing (in seconds,
    relative to the start of the spoken portion of this
    scene's audio - i.e. BEFORE any trailing pause silence)
    of each word Azure actually spoke. This is what lets the
    video generator sync the board-writing animation to the
    actual recorded voice, word by word. It will be an empty
    list if no boundary events were captured for some reason
    - callers must be able to handle that and fall back
    gracefully.
    """

    text = clean_text(
        text
    )

    text, marker_pause_seconds = _extract_pause_markers(
        text
    )

    total_pause_seconds = float(
        pause_seconds
    ) + marker_pause_seconds

    if not text and total_pause_seconds <= 0:

        raise ValueError(
            "Text-to-speech text is empty."
        )

    # De-duplicate consecutive repeated sentences, then
    # rejoin into a SINGLE block of text so the whole scene
    # is synthesized in one continuous voice call - this is
    # what keeps the tone consistent and removes seams
    # between separately-synthesized clips.

    sentences = split_into_sentences(text)

    speech_text = " ".join(sentences) if sentences else text

    output_path = Path(
        output_path
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    speech_mp3_path = (
        output_path.parent
        /
        f"{output_path.stem}_speech.mp3"
    )

    silence_mp3_path = (
        output_path.parent
        /
        f"{output_path.stem}_pause.mp3"
    )

    voice = _select_voice(language)

    locale = _select_locale(language)

    normalized_rate = _normalize_rate(rate)

    have_speech_clip = False

    word_boundaries = []

    if speech_text:

        word_boundaries = _run_synthesis_with_boundaries(
            speech_text,
            voice,
            locale,
            normalized_rate,
            speech_mp3_path,
        )

        if not speech_mp3_path.exists():

            raise RuntimeError(
                "Azure text-to-speech did not produce an "
                "audio file."
            )

        have_speech_clip = True

    have_silence_clip = False

    if total_pause_seconds > 0:

        silence_command = _build_silence_command(
            total_pause_seconds,
            silence_mp3_path,
        )

        silence_result = subprocess.run(
            silence_command,
            capture_output=True,
            text=True,
        )

        if silence_result.returncode != 0:

            raise RuntimeError(
                "Failed to generate the silent pause clip:\n"
                + silence_result.stderr
            )

        have_silence_clip = silence_mp3_path.exists()

    # ------------------------------------------------------
    # Combine speech + silence (if any) into the final file.
    # ------------------------------------------------------

    if have_speech_clip and have_silence_clip:

        concat_list_path = (
            output_path.parent
            /
            f"{output_path.stem}_concat.txt"
        )

        concat_list_path.write_text(
            "file '{}'\nfile '{}'\n".format(
                speech_mp3_path.name,
                silence_mp3_path.name,
            ),
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

        speech_mp3_path.replace(
            output_path
        )

    elif have_silence_clip:

        silence_mp3_path.replace(
            output_path
        )

    for temp_file in (
        speech_mp3_path,
        silence_mp3_path,
    ):

        try:

            if temp_file.exists():

                temp_file.unlink()

        except Exception:

            pass

    if not output_path.exists():

        raise RuntimeError(
            "MP3 audio file was not created."
        )

    return {
        "path": str(output_path),
        "word_boundaries": word_boundaries,
    }
