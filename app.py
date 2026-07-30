"""
Birhan AI
Main Flask Application

Generates lessons, creates scene visuals,
generates audio, and produces synchronized
educational videos.

============================================================
CHANGES IN THIS VERSION
============================================================

1. VISUAL STYLE ASSIGNMENT FIX: services/board_renderer.py defines
   10 board themes and 9 camera angles, switched only at teaching
   phase boundaries (so a board always finishes ALL of its writing
   before switching), but nothing ever called
   board_renderer.assign_visual_style() on the finished scene list -
   so every scene silently fell back to the same single default
   theme/camera. It is now called ONCE, on the COMPLETE scene list
   (including the Phase 10 assessment scenes and the final closing
   scene), immediately before any scene image or audio is generated,
   exactly as services/board_renderer.py's own docstring requires.
   Because services/visual_generator.py (gallery image) and
   services/video_generator.py (video frames) both read a scene's
   "board_theme"/"camera_angle" from the scene dict itself, doing
   this assignment once, up front, is what keeps the gallery image
   and the video frame for every scene in agreement.

2. DEPLOYMENT: the __main__ entry point now binds to 0.0.0.0 and
   reads PORT from the environment (falling back to 5000 locally),
   and debug mode is now controlled by the FLASK_DEBUG environment
   variable (defaulting to off) instead of being hard-coded on -
   both required for a safe Render.com deployment. Render.com is
   expected to actually run the app via the Procfile
   ("gunicorn app:app"), which never executes this block at all;
   it is kept correct for local development and any environment
   that runs `python app.py` directly.

3. TTS ENGINE FALLBACK VISIBILITY: services/text_to_speech.py now
   automatically falls back to gTTS for any scene where edge-tts
   fails (e.g. the Microsoft backend returning a 403, or a
   cloud-host IP being blocked), and reports which engine
   ("edge-tts" or "gtts") actually produced each scene's audio.
   This version tracks that per lesson: every fallback is logged
   via app.logger.warning() (so it shows up in Render's logs
   immediately instead of only being noticeable from a changed
   voice or missing board-sync timing), and a simple
   `tts_fallback_count` / `tts_fallback_used` pair is passed to
   lesson.html so the template can optionally show a small notice
   when part of a lesson had to use the fallback voice.

Everything else - lesson generation, scene splitting, tiered
assessment scene construction, audio caching, video assembly,
lesson saving/rendering - is unchanged from the previous version.
"""

import json
import os
import shutil
import uuid

from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
)

from config import (
    SECRET_KEY,
    AUDIO_DIR,
    GENERATED_IMAGE_DIR,
    VIDEO_DIR,
    APPLAUSE_PAUSE_SECONDS,
    THINKING_PAUSE_SECONDS,
    CLOSING_PAUSE_SECONDS,
)

from services.ai_engine import (
    generate_lesson,
    REQUIRED_CLOSING_PHRASE,
)

from services.text_to_speech import (
    generate_audio,
)

from services.lesson_parser import (
    split_into_scenes,
)

from services.visual_detector import (
    detect_visual_type,
)

from services.visual_generator import (
    create_scene_visual,
)

from services.video_generator import (
    create_video,
)

from services.board_renderer import (
    assign_visual_style,
)

from models.database import (
    initialize_database,
    save_lesson,
)


# ============================================================
# CREATE FLASK APP
# ============================================================

app = Flask(
    __name__
)


# ============================================================
# SECRET KEY
# ============================================================

app.config["SECRET_KEY"] = SECRET_KEY


# ============================================================
# INITIALIZE DATABASE
# ============================================================

initialize_database()


# ============================================================
# NARRATION SPEAKING RATE
# ============================================================

NARRATION_RATE = "-25%"


# ============================================================
# ASSESSMENT TIER ORDER / LABELS
#
# Phase 10 requires the assessment to move from easy, to
# moderate, to difficult - this order is fixed, not something
# the AI or the lesson data can reorder.
# ============================================================

ASSESSMENT_TIERS = [
    ("easy", "an easy question"),
    ("moderate", "a moderate question"),
    ("difficult", "a challenging question"),
]


# ============================================================
# TIERED ASSESSMENT SCENE HELPERS (PHASE 10)
#
# Builds ask -> think -> answer(+applause pause) scene triples
# for one difficulty tier at a time, using the same
# "ask everything first, then go back and answer everything"
# rhythm as before. Multiple-choice items (those with
# "options") get every option shown on the board; open-ended
# items just show the question.
# ============================================================

def _build_question_text(item):

    question = str(item.get("question", "")).strip()

    options = item.get("options", [])

    if not isinstance(options, list):

        options = []

    option_letters = ["A", "B", "C", "D", "E", "F"]

    option_lines = []

    for letter, option in zip(option_letters, options):

        option_text = str(option).strip()

        if option_text:

            option_lines.append(f"{letter}. {option_text}")

    if option_lines:

        return question + " " + " ".join(option_lines)

    return question


def _build_question_board_text(item):

    """
    Same content as _build_question_text, but formatted as
    separate lines for the board, so the full question - and
    every option, for multiple choice - is clearly visible
    together, from start to finish.
    """

    question = str(item.get("question", "")).strip()

    options = item.get("options", [])

    if not isinstance(options, list):

        options = []

    option_letters = ["A", "B", "C", "D", "E", "F"]

    lines = [question] if question else []

    for letter, option in zip(option_letters, options):

        option_text = str(option).strip()

        if option_text:

            lines.append(f"{letter}. {option_text}")

    return "\n".join(lines)


def _build_tier_scenes(tier_name, tier_label, items, starting_scene_number):

    """
    Builds the ask -> think -> answer(+applause) scenes for a
    single assessment tier (easy / moderate / difficult),
    asking every question in the tier first, then going back
    to answer every one of them - matching a real classroom
    review rhythm.
    """

    if not items:

        return [], starting_scene_number

    question_scenes = []

    answer_scenes = []

    scene_number = starting_scene_number

    tier_title = tier_name.capitalize() + " Questions"

    for index, item in enumerate(items):

        question = str(item.get("question", "")).strip()

        answer = str(item.get("answer", "")).strip()

        explanation = str(item.get("explanation", "")).strip()

        if not question or not answer:

            # No specific, real answer available - skip rather
            # than invent a generic placeholder sentence.

            continue

        # ----------------------------------------------------
        # ASK THE QUESTION
        # ----------------------------------------------------

        spoken_question = _build_question_text(item)

        board_question = _build_question_board_text(item)

        if index == 0:

            intro = (
                f"Now let's try {tier_label}s. "
            )

        else:

            intro = "Next question. "

        question_scenes.append(

            {

                "scene_number": scene_number,

                "type": f"assessment_{tier_name}",

                "title": tier_title[:60],

                "narration": intro + spoken_question,

                "text": intro + spoken_question,

                "board_text": board_question,

                "visual_type": "educational",

            }

        )

        scene_number += 1

        # ----------------------------------------------------
        # THINKING PERIOD
        # ----------------------------------------------------

        question_scenes.append(

            {

                "scene_number": scene_number,

                "type": f"assessment_{tier_name}",

                "title": "Think About Your Answer",

                "narration": (

                    "Take a moment to think about your answer."

                    f" [PAUSE:{THINKING_PAUSE_SECONDS}]"

                ),

                "text": "Think about your answer...",

                "board_text": "Think about your answer...",

                "visual_type": "educational",

                "pause_duration": THINKING_PAUSE_SECONDS,

            }

        )

        scene_number += 1

        # ----------------------------------------------------
        # ANSWER, EXPLANATION, AND APPLAUSE PAUSE
        # (built now, appended after every question in this
        # tier has been asked)
        # ----------------------------------------------------

        answer_text = (

            "For question "
            + str(index + 1)
            + ": the correct answer is "
            + answer
            + ". "
            + explanation

        ).strip()

        applause_line = (

            "If your answer for this question was "
            + answer
            + ", excellent job! Give yourselves a round of applause!"
            + f" [PAUSE:{APPLAUSE_PAUSE_SECONDS}]"

        )

        answer_scenes.append(

            {

                "scene_number": scene_number,

                "type": f"assessment_{tier_name}",

                "title": "Correct Answer",

                "narration": answer_text + " " + applause_line,

                "text": answer_text,

                "board_text": answer_text,

                "visual_type": "educational",

                "pause_duration": APPLAUSE_PAUSE_SECONDS,

            }

        )

        scene_number += 1

    return question_scenes + answer_scenes, scene_number


def create_assessment_scenes(lesson, starting_scene_number):

    """
    Builds the complete Phase 10 assessment: easy tier first
    (ask all, then answer all), then moderate, then difficult -
    using sequential scene numbers continuing from
    `starting_scene_number`.
    """

    assessment = lesson.get("assessment", {})

    if not isinstance(assessment, dict):

        return [], starting_scene_number

    scenes = []

    scene_number = starting_scene_number

    for tier_name, tier_label in ASSESSMENT_TIERS:

        tier_items = assessment.get(tier_name, [])

        if not isinstance(tier_items, list):

            continue

        tier_scenes, scene_number = _build_tier_scenes(

            tier_name,

            tier_label,

            tier_items,

            scene_number,

        )

        scenes.extend(tier_scenes)

    return scenes, scene_number


# ============================================================
# FINAL CLOSING SCENE
#
# Builds the very last scene of the video, using the EXACT
# required closing sentence. This does not depend on what the
# AI returned for "closing_phrase" - it always uses the same
# constant from services/ai_engine.py, so the wording is
# guaranteed correct every time.
# ============================================================

def create_closing_scene(starting_scene_number):

    scene = {

        "scene_number": starting_scene_number,

        "type": "closing",

        "title": "Closing",

        "narration": REQUIRED_CLOSING_PHRASE,

        "text": REQUIRED_CLOSING_PHRASE,

        "board_text": REQUIRED_CLOSING_PHRASE,

        "visual_type": "classroom",

        "pause_duration": CLOSING_PAUSE_SECONDS,

    }

    return [scene], starting_scene_number + 1


# ============================================================
# HOME PAGE
# ============================================================

@app.route("/")
def index():

    return render_template(
        "index.html"
    )


# ============================================================
# GENERATE LESSON
# ============================================================

@app.route(
    "/generate-lesson",
    methods=["POST"],
)
def generate_lesson_route():

    # ========================================================
    # GET FORM DATA
    # ========================================================

    topic = request.form.get(
        "topic",
        "",
    ).strip()


    grade = request.form.get(
        "grade",
        "Grade 7",
    ).strip()


    subject = request.form.get(
        "subject",
        "General Science",
    ).strip()


    chapter = request.form.get(
        "chapter",
        "",
    ).strip()


    previous_topic = request.form.get(
        "previous_topic",
        "",
    ).strip()


    language = request.form.get(
        "language",
        "English",
    ).strip()


    source_text = request.form.get(
        "source_text",
        "",
    ).strip()


    # ========================================================
    # VALIDATE TOPIC
    # ========================================================

    if not topic:

        flash(
            "Please enter a lesson topic."
        )

        return redirect(
            url_for(
                "index"
            )
        )


    try:

        # ====================================================
        # 1. GENERATE LESSON
        # ====================================================

        lesson = generate_lesson(

            topic=topic,

            grade=grade,

            subject=subject,

            language=language,

            source_text=source_text,

            chapter=chapter,

            previous_topic=previous_topic,

        )


        if not isinstance(
            lesson,
            dict
        ):

            raise ValueError(

                "The AI did not return a valid lesson."

            )


        # ====================================================
        # 2. SPLIT LESSON INTO SCENES
        #
        # NOTE: services/lesson_parser.py (split_into_scenes)
        # builds every phase of the teaching model EXCEPT the
        # Phase 10 tiered assessment and the final closing
        # scene - those are appended below so their scene
        # numbers can continue on sequentially afterward.
        # ====================================================

        scenes = split_into_scenes(
            lesson
        )


        if not scenes:

            raise ValueError(

                "No scenes were created from the lesson."

            )


        # ====================================================
        # 3. FIGURE OUT THE NEXT FREE SCENE NUMBER
        # ====================================================

        highest_scene_number = 0

        for index, scene in enumerate(scenes):

            if not isinstance(scene, dict):

                continue

            scene_number = scene.get("scene_number", index + 1)

            try:

                scene_number = int(scene_number)

            except (TypeError, ValueError):

                scene_number = index + 1

            scene["scene_number"] = scene_number

            highest_scene_number = max(
                highest_scene_number,
                scene_number
            )

        next_scene_number = highest_scene_number + 1


        # ====================================================
        # 4. ADD PHASE 10 TIERED ASSESSMENT SCENES
        #    (easy -> moderate -> difficult, each tier asked
        #    in full, then answered in full)
        # ====================================================

        assessment_scenes, next_scene_number = create_assessment_scenes(
            lesson,
            next_scene_number
        )

        if assessment_scenes:

            scenes.extend(
                assessment_scenes
            )


        # ====================================================
        # 5. ADD THE FINAL CLOSING SCENE
        #
        # Always the very last scene, always the exact
        # required closing sentence.
        # ====================================================

        closing_scenes, next_scene_number = create_closing_scene(
            next_scene_number
        )

        scenes.extend(
            closing_scenes
        )


        # ====================================================
        # 5b. ASSIGN BOARD THEME / CAMERA ANGLE FOR THE WHOLE
        #     LESSON (10 themes, 9 camera angles), grouped by
        #     teaching phase so a board only ever switches once
        #     everything assigned to it has finished being
        #     written. Must run on the COMPLETE scene list,
        #     before any scene image or audio is generated, so
        #     the gallery image and the video frame for every
        #     scene are guaranteed to agree.
        # ====================================================

        assign_visual_style(
            scenes
        )


        # ====================================================
        # 6. CREATE UNIQUE LESSON ID
        # ====================================================

        lesson_uuid = uuid.uuid4().hex


        scene_images = []


        scene_audios = []


        valid_scenes = []


        # ====================================================
        # PERFORMANCE: identical narration text (e.g. the
        # repeated "Think about your answer..." / "Next
        # question." lines used by the assessment scenes) is
        # cached by the exact (text, language, rate, pause)
        # combination, so each unique line is only synthesized
        # once per lesson; every repeat is a plain file copy
        # instead of a full TTS run. The cache also stores each
        # line's real per-word timing ("word_boundaries") and
        # which TTS engine produced it, so a repeated line
        # reuses its correct timing and fallback status too.
        # ====================================================

        audio_cache = {}


        # ====================================================
        # TTS ENGINE FALLBACK TRACKING
        #
        # generate_audio() automatically falls back to gTTS if
        # edge-tts fails for a scene (e.g. a 403 from
        # Microsoft's backend, or the host's IP being blocked).
        # Every time that happens for a NEWLY synthesized line
        # (not a cache hit - the cache already reflects the
        # engine that was actually used the first time), it is
        # logged immediately via app.logger.warning() so it is
        # visible in Render's logs right away, and counted so
        # the lesson page can optionally show a small notice.
        # ====================================================

        tts_fallback_count = 0


        # ====================================================
        # 7. CREATE SCENE IMAGES AND AUDIO
        # ====================================================

        for index, scene in enumerate(

            scenes

        ):


            if not isinstance(
                scene,
                dict
            ):

                continue


            scene_number = scene.get(

                "scene_number",

                index + 1

            )


            # =================================================
            # DETECT VISUAL TYPE
            # =================================================

            visual_type = detect_visual_type(

                scene

            )


            if not visual_type:

                visual_type = (

                    scene.get(

                        "visual_type",

                        "educational"

                    )

                )


            scene["visual_type"] = visual_type


            # =================================================
            # CREATE SCENE IMAGE
            # =================================================

            image_filename = (

                f"scene_{scene_number}_"

                f"{lesson_uuid}.png"

            )


            image_path = (

                Path(

                    GENERATED_IMAGE_DIR

                )

                /

                image_filename

            )


            create_scene_visual(

                scene=scene,

                output_path=image_path,

            )


            scene_images.append(

                image_path

            )


            # =================================================
            # GET NARRATION TEXT
            # =================================================

            scene_text = scene.get(

                "narration",

                ""

            )


            if not scene_text:

                scene_text = (

                    scene.get(

                        "text",

                        ""

                    )

                )


            if not scene_text:

                scene_text = (

                    f"Scene {scene_number}"

                )


            scene_text = str(

                scene_text

            ).strip()


            # =================================================
            # CREATE AUDIO
            #
            # `pause_duration` (set on thinking / answer /
            # closing scenes) is passed through so
            # generate_audio() appends real silence after the
            # spoken text - e.g. time for students to think or
            # applaud.
            #
            # generate_audio() returns the real per-word timing
            # it captured while synthesizing this scene's
            # narration, plus which engine ("edge-tts" or
            # "gtts") actually produced the audio. The timing
            # is attached to the scene itself as
            # "speech_word_boundaries" so
            # services/video_generator.py can reveal each word
            # on the board at the exact instant it is actually
            # spoken; the engine is used only for logging/
            # tracking the fallback here.
            # =================================================

            audio_filename = (

                f"scene_{scene_number}_"

                f"{lesson_uuid}.mp3"

            )

            audio_path = (

                Path(

                    AUDIO_DIR

                )

                /

                audio_filename

            )

            pause_seconds = scene.get("pause_duration", 0) or 0

            cache_key = (
                scene_text.strip().lower(),
                language,
                NARRATION_RATE,
                pause_seconds,
            )

            cached_entry = audio_cache.get(cache_key)

            if cached_entry is not None:

                # Same text + language + rate + pause has
                # already been synthesized this lesson - reuse
                # that file (and its timing data) instead of
                # running TTS again. Already counted toward
                # tts_fallback_count the first time, if it used
                # the fallback engine, so it is not counted
                # again here.

                cached_audio_path, cached_word_boundaries, _cached_engine = cached_entry

                shutil.copyfile(
                    cached_audio_path,
                    audio_path,
                )

                word_boundaries = cached_word_boundaries

            else:

                audio_result = generate_audio(

                    text=scene_text,

                    output_path=audio_path,

                    language=language,

                    rate=NARRATION_RATE,

                    pause_seconds=pause_seconds,

                )

                word_boundaries = (

                    audio_result.get("word_boundaries", [])

                    if isinstance(audio_result, dict)

                    else []

                )

                engine_used = (

                    audio_result.get("engine")

                    if isinstance(audio_result, dict)

                    else None

                )

                if engine_used == "gtts":

                    tts_fallback_count += 1

                    app.logger.warning(

                        "Scene %s fell back to gTTS "

                        "(edge-tts unavailable) - lesson %s",

                        scene_number,

                        lesson_uuid,

                    )

                audio_cache[cache_key] = (
                    audio_path,
                    word_boundaries,
                    engine_used,
                )


            scene["speech_word_boundaries"] = word_boundaries


            scene_audios.append(

                audio_path

            )


            valid_scenes.append(

                scene

            )


        # ====================================================
        # VALIDATE GENERATED FILES
        # ====================================================

        if not scene_images:

            raise ValueError(

                "No scene images were generated."

            )


        if not scene_audios:

            raise ValueError(

                "No scene audio files were generated."

            )


        if len(

            scene_images

        ) != len(

            scene_audios

        ):

            raise ValueError(

                "The number of scene images and audio files "

                "do not match."

            )


        scenes = valid_scenes


        # ====================================================
        # 8. CREATE COMPLETE VIDEO
        # ====================================================

        video_filename = (

            f"lesson_{lesson_uuid}.mp4"

        )


        video_path = (

            Path(

                VIDEO_DIR

            )

            /

            video_filename

        )


        create_video(

            image_files=scene_images,

            audio_files=scene_audios,

            output_file=video_path,

            scenes=scenes,

        )


        # ====================================================
        # 9. MAIN AUDIO
        # ====================================================

        main_audio_filename = (

            Path(

                scene_audios[0]

            ).name

            if scene_audios

            else ""

        )


        # ====================================================
        # 10. SAVE LESSON
        # ====================================================

        lesson_id = save_lesson(

            title=lesson.get(

                "title",

                topic

            ),

            subject=subject,

            grade=grade,

            language=language,

            lesson_json=json.dumps(

                lesson,

                ensure_ascii=False,

            ),

            audio_filename=main_audio_filename,

            video_filename=video_filename,

        )


        # ====================================================
        # 11. IMAGE FILENAMES
        # ====================================================

        scene_image_names = [

            Path(

                image

            ).name

            for image in scene_images

        ]


        # ====================================================
        # 12. AUDIO FILENAMES
        # ====================================================

        scene_audio_names = [

            Path(

                audio

            ).name

            for audio in scene_audios

        ]


        # ====================================================
        # 13. LOG A LESSON-LEVEL SUMMARY IF ANY SCENE FELL
        #     BACK TO gTTS, SO IT'S EASY TO SPOT IN RENDER'S
        #     LOGS WITHOUT SCROLLING THROUGH PER-SCENE LINES.
        # ====================================================

        if tts_fallback_count > 0:

            app.logger.warning(

                "Lesson %s finished with %s scene(s) using "

                "the gTTS fallback voice instead of edge-tts.",

                lesson_uuid,

                tts_fallback_count,

            )


        # ====================================================
        # 14. SHOW LESSON
        # ====================================================

        return render_template(

            "lesson.html",

            lesson=lesson,

            lesson_id=lesson_id,

            audio_filename=main_audio_filename,

            video_filename=video_filename,

            scene_images=scene_image_names,

            scene_audios=scene_audio_names,

            tts_fallback_count=tts_fallback_count,

            tts_fallback_used=tts_fallback_count > 0,

        )


    except Exception as error:


        app.logger.exception(

            "Lesson generation failed"

        )


        flash(

            f"Error: {error}"

        )


        return redirect(

            url_for(

                "index"

            )

        )


# ============================================================
# RUN APPLICATION
#
# Render.com deployment note: the Procfile runs this app with
# gunicorn ("gunicorn app:app"), which never executes this
# __main__ block at all. It is kept correct here only for local
# development / any environment that runs `python app.py`
# directly - binding to 0.0.0.0 and honoring the PORT
# environment variable (which Render also sets for services that
# do run via `python app.py`), and keeping debug mode OFF unless
# FLASK_DEBUG=1 is explicitly set.
# ============================================================

if __name__ == "__main__":

    debug_mode = os.getenv("FLASK_DEBUG", "0").strip() == "1"

    port = int(os.getenv("PORT", "5000"))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=debug_mode,
    )
