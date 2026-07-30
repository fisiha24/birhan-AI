"""
Birhan AI
Text-to-Speech Service

NEURAL VOICE VERSION (Microsoft Edge neural voices via
the `edge-tts` package)

Each scene receives its own audio file.

============================================================
WHY THIS VERSION EXISTS
============================================================

The previous version used the Windows SAPI5 desktop voices
(via PowerShell). SAPI5 voices sound robotic, and the old
per-sentence rate/volume switching (meant to add "teacher
energy") caused audible jumps in tone between sentences,
because SAPI5 resets its speaking rate at the start of every
separate $synth.Speak() call.

This version instead uses a single Microsoft Edge neural
voice (natural, human-sounding) per language, spoken in ONE
continuous synthesis call per scene:

1. Natural voice: neural TTS instead of SAPI5.
2. Consistent tone: one voice, one rate, for the entire
   scene - no per-sentence style switching.
3. Clear, slightly slowed pacing so students can follow.
4. No mid-sentence stutters/glitches: because the whole
   scene's narration is sent to the synthesizer in a single
   call, there are no seams between separately-synthesized
   sentence clips (which is what caused audible "stutter" in
   the old version).
5. Duplicate consecutive sentences are still removed before
   synthesis (e.g. an accidentally repeated intro line).
6. Real silent pauses (e.g. time for students to applaud)
   are still supported via `pause_seconds` and/or a
   "[PAUSE:n]" marker inside the text, appended with ffmpeg
   after the spoken audio.

============================================================
NOTE FOR THIS UPDATE
============================================================

edge-tts talks to an unofficial Microsoft endpoint
(speech.platform.bing.com) that periodically starts
rejecting connections with a 403 "Invalid response status"
error - either because Microsoft changed the auth token
scheme, or because the request came from a cloud/data-center
IP range. This is out of our control and happens even with a
correct setup, so this version adds a GTTS FALLBACK: if
edge-tts fails for any reason, we automatically retry the
same scene through gTTS (Google Translate TTS) so the app
still produces a usable lesson instead of crashing.

gTTS does not provide per-word timing data, so scenes that
fall back to it will not have `word_boundaries` - the video
generator already knows to fall back to its own evenly-spread
pacing when `word_boundaries` is empty.

============================================================
REQUIREMENTS
============================================================

pip install edge-tts gTTS --break-system-packages

`ffmpeg` must be available on PATH (already required by the
rest of the app for video/audio work).
"""

import asyncio
import re

from pathlib import Path

import subprocess

import edge_tts

from gtts import gTTS


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
# suddenly changes tone or character partway through.
# ============================================================

LANGUAGE_VOICE_MAP = {

    "english": "en-US-GuyNeural",

    "amharic": "am-ET-AmehaNeural",

}

DEFAULT_VOICE = "en-US-GuyNeural"


def _select_voice(language):

    key = str(language or "").strip().lower()

    return LANGUAGE_VOICE_MAP.get(
        key,
        DEFAULT_VOICE,
    )


# ============================================================
# gTTS LANGUAGE SELECTION
#
# gTTS uses plain ISO language codes rather than edge-tts's
# "xx-XX-NameNeural" voice names, so we need a separate map
# for the fallback path.
# ============================================================

LANGUAGE_GTTS_MAP = {

    "english": "en",

    "amharic": "am",

}

DEFAULT_GTTS_LANGUAGE = "en"


def _select_gtts_language(language):

    key = str(language or "").strip().lower()

    return LANGUAGE_GTTS_MAP.get(
        key,
        DEFAULT_GTTS_LANGUAGE,
    )


# ============================================================
# RATE MAPPING
#
# edge-tts accepts the same "+n%"/"-n%" style rate strings
# the rest of this app already uses (e.g. "-25%"), so the
# value is passed straight through. A negative rate keeps
# speech clear, slow, and easy for students to follow, and -
# unlike the old SAPI5 version - it is applied ONCE for the
# whole scene rather than jumping around per sentence.
#
# PACING FIX: the default was "-10%", which still read as
# fairly quick for a classroom lesson. It is now "-25%" so
# the teacher speaks at a clearly slower, more deliberate
# pace by default. Callers (see app.py) also pass this same
# slower rate explicitly.
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
# Generated at 24000 Hz mono to match edge-tts's own speech
# output sample rate, so the two segments join cleanly with
# no audible seam when concatenated.
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
# RUN EDGE-TTS SYNTHESIS - PLAIN (NO TIMING DATA)
#
# Kept as the fallback synthesis path used only if the
# streaming/WordBoundary path below fails for some reason
# (e.g. an older edge-tts version). This never returns timing
# data, so any scene synthesized this way will fall back to
# the video generator's evenly-spread board-writing pace.
# ============================================================

async def _synthesize(text, voice, rate, output_path):

    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
    )

    await communicate.save(
        str(output_path)
    )


def _run_synthesis(text, voice, rate, output_path):

    asyncio.run(
        _synthesize(
            text,
            voice,
            rate,
            output_path,
        )
    )


# ============================================================
# RUN EDGE-TTS SYNTHESIS - WITH REAL WORD TIMING
#
# Uses edge-tts's streaming API so we can capture
# "WordBoundary" events as they are produced - these carry the
# REAL offset/duration (in 100-nanosecond units) of every word
# inside the synthesized audio. That gives us the actual
# pacing of the recorded voice, including any natural
# micro-pauses the voice engine adds, instead of an assumed
# constant speaking rate. services/video_generator.py uses
# this to align every board word to the instant it is spoken.
# ============================================================

async def _synthesize_with_boundaries(text, voice, rate, output_path):

    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=rate,
    )

    boundaries = []

    with open(output_path, "wb") as audio_file:

        async for chunk in communicate.stream():

            chunk_type = chunk.get("type")

            if chunk_type == "audio":

                data = chunk.get("data")

                if data:

                    audio_file.write(data)

            elif chunk_type == "WordBoundary":

                offset_100ns = chunk.get("offset", 0) or 0

                duration_100ns = chunk.get("duration", 0) or 0

                start_seconds = float(offset_100ns) / 10_000_000.0

                duration_seconds = float(duration_100ns) / 10_000_000.0

                boundaries.append(
                    {
                        "text": chunk.get("text", ""),
                        "start": start_seconds,
                        "end": start_seconds + duration_seconds,
                    }
                )

    return boundaries


def _run_synthesis_with_boundaries(text, voice, rate, output_path):

    return asyncio.run(
        _synthesize_with_boundaries(
            text,
            voice,
            rate,
            output_path,
        )
    )


# ============================================================
# RUN gTTS SYNTHESIS - FALLBACK WHEN EDGE-TTS IS UNAVAILABLE
#
# gTTS talks to Google Translate's TTS endpoint instead of
# Microsoft's, so it keeps working when edge-tts's unofficial
# backend is rejecting connections (403s, cloud-IP blocking,
# token-scheme changes, etc.). It has no rate/speed control
# and no per-word timing data - callers get an empty
# `word_boundaries` list for any scene synthesized this way,
# and the video generator already falls back to its own
# evenly-spread pacing in that case.
# ============================================================

def _run_synthesis_with_gtts(text, language, output_path):

    gtts_language = _select_gtts_language(language)

    tts = gTTS(
        text=text,
        lang=gtts_language,
    )

    tts.save(
        str(output_path)
    )


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
        "engine": "edge-tts" | "gtts",
      }

    `word_boundaries` gives the REAL timing (in seconds,
    relative to the start of the spoken portion of this
    scene's audio - i.e. BEFORE any trailing pause silence)
    of each word edge-tts actually spoke. This is what lets
    the video generator sync the board-writing animation to
    the actual recorded voice, word by word. It will be an
    empty list if timing data could not be captured - either
    because edge-tts's streaming path failed and we fell back
    to its plain save() call, or because the scene fell all
    the way back to gTTS. Callers must be able to handle that
    and fall back gracefully (the video generator already
    does).

    ENGINE FALLBACK: edge-tts depends on an unofficial
    Microsoft endpoint that can reject connections at any time
    (403 errors, cloud-IP blocking, changed auth tokens). If
    edge-tts fails for ANY reason, this function automatically
    retries the same scene through gTTS so lesson generation
    does not crash. The `engine` key in the returned dict
    tells the caller which one actually produced the audio.
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
    # what keeps the tone consistent and removes the seams
    # (stutters) that came from stitching many small clips.

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

    normalized_rate = _normalize_rate(rate)

    have_speech_clip = False

    word_boundaries = []

    engine_used = None

    if speech_text:

        try:

            word_boundaries = _run_synthesis_with_boundaries(
                speech_text,
                voice,
                normalized_rate,
                speech_mp3_path,
            )

            engine_used = "edge-tts"

        except Exception:

            # The streaming (WordBoundary) path failed - try
            # edge-tts's plain save() call before giving up on
            # edge-tts entirely. This still counts as the
            # edge-tts engine; it just won't have timing data.

            try:

                _run_synthesis(
                    speech_text,
                    voice,
                    normalized_rate,
                    speech_mp3_path,
                )

                word_boundaries = []

                engine_used = "edge-tts"

            except Exception:

                # edge-tts is unavailable (e.g. the Microsoft
                # backend returned a 403, or the connection was
                # blocked/rejected). Fall back to gTTS so the
                # scene still gets audio instead of crashing
                # the whole lesson. No timing data is available
                # from this path.

                _run_synthesis_with_gtts(
                    speech_text,
                    language,
                    speech_mp3_path,
                )

                word_boundaries = []

                engine_used = "gtts"

        if not speech_mp3_path.exists():

            raise RuntimeError(
                "Text-to-speech did not produce an audio file "
                "(both edge-tts and the gTTS fallback failed)."
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
        "engine": engine_used,
    }
