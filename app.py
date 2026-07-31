"""
Birhan AI
Main Flask Application

Generates lessons, creates scene visuals,
generates gTTS audio, and produces synchronized
educational videos.

Deployment:
    gunicorn --workers 1 --threads 2 --timeout 600 app:app
"""

import json
import os
import shutil
import threading
import uuid

from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
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

app = Flask(__name__)

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
# ASSESSMENT TIER ORDER
# ============================================================

ASSESSMENT_TIERS = [
    ("easy", "an easy question"),
    ("moderate", "a moderate question"),
    ("difficult", "a challenging question"),
]


# ============================================================
# IN-MEMORY JOB STATUS STORE
#
# Simple in-process dict. Works because gunicorn is run with
# --workers 1 (a single process), so all requests share the
# same memory space. If you ever move to more than one worker,
# this must be replaced with something shared across processes
# (e.g. a database table or Redis) since each worker would
# otherwise have its own separate copy of this dict.
# ============================================================

LESSON_JOBS = {}


# ============================================================
# BUILD QUESTION TEXT
# ============================================================

def _build_question_text(item):

    question = str(
        item.get("question", "")
    ).strip()

    options = item.get(
        "options",
        []
    )

    if not isinstance(
        options,
        list
    ):
        options = []

    option_letters = [
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
    ]

    option_lines = []

    for letter, option in zip(
        option_letters,
        options,
    ):

        option_text = str(
            option
        ).strip()

        if option_text:

            option_lines.append(
                f"{letter}. {option_text}"
            )

    if option_lines:

        return (
            question
            + " "
            + " ".join(option_lines)
        )

    return question


# ============================================================
# BUILD QUESTION BOARD TEXT
# ============================================================

def _build_question_board_text(item):

    question = str(
        item.get("question", "")
    ).strip()

    options = item.get(
        "options",
        []
    )

    if not isinstance(
        options,
        list
    ):
        options = []

    option_letters = [
        "A",
        "B",
        "C",
        "D",
        "E",
        "F",
    ]

    lines = []

    if question:
        lines.append(question)

    for letter, option in zip(
        option_letters,
        options,
    ):

        option_text = str(
            option
        ).strip()

        if option_text:

            lines.append(
                f"{letter}. {option_text}"
            )

    return "\n".join(lines)


# ============================================================
# BUILD TIER ASSESSMENT SCENES
# ============================================================

def _build_tier_scenes(
    tier_name,
    tier_label,
    items,
    starting_scene_number,
):

    if not items:

        return [], starting_scene_number

    question_scenes = []
    answer_scenes = []

    scene_number = starting_scene_number

    tier_title = (
        tier_name.capitalize()
        + " Questions"
    )

    for index, item in enumerate(items):

        if not isinstance(
            item,
            dict
        ):
            continue

        question = str(
            item.get(
                "question",
                "",
            )
        ).strip()

        answer = str(
            item.get(
                "answer",
                "",
            )
        ).strip()

        explanation = str(
            item.get(
                "explanation",
                "",
            )
        ).strip()

        if not question or not answer:

            continue

        # ----------------------------------------------------
        # ASK QUESTION
        # ----------------------------------------------------

        spoken_question = _build_question_text(
            item
        )

        board_question = _build_question_board_text(
            item
        )

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
                "narration": (
                    intro
                    + spoken_question
                ),
                "text": (
                    intro
                    + spoken_question
                ),
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
                    "Take a moment to think "
                    "about your answer."
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
        # ANSWER
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
            + ", excellent job! "
            + "Give yourselves a round of applause!"
            + f" [PAUSE:{APPLAUSE_PAUSE_SECONDS}]"
        )

        answer_scenes.append(
            {
                "scene_number": scene_number,
                "type": f"assessment_{tier_name}",
                "title": "Correct Answer",
                "narration": (
                    answer_text
                    + " "
                    + applause_line
                ),
                "text": answer_text,
                "board_text": answer_text,
                "visual_type": "educational",
                "pause_duration": APPLAUSE_PAUSE_SECONDS,
            }
        )

        scene_number += 1

    return (
        question_scenes + answer_scenes,
        scene_number,
    )


# ============================================================
# CREATE ASSESSMENT SCENES
# ============================================================

def create_assessment_scenes(
    lesson,
    starting_scene_number,
):

    assessment = lesson.get(
        "assessment",
        {},
    )

    if not isinstance(
        assessment,
        dict
    ):

        return [], starting_scene_number

    scenes = []

    scene_number = starting_scene_number

    for tier_name, tier_label in ASSESSMENT_TIERS:

        tier_items = assessment.get(
            tier_name,
            []
        )

        if not isinstance(
            tier_items,
            list
        ):
            continue

        tier_scenes, scene_number = _build_tier_scenes(
            tier_name,
            tier_label,
            tier_items,
            scene_number,
        )

        scenes.extend(
            tier_scenes
        )

    return scenes, scene_number


# ============================================================
# CREATE FINAL CLOSING SCENE
# ============================================================

def create_closing_scene(
    starting_scene_number
):

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

    return [
        scene
    ], starting_scene_number + 1


# ============================================================
# HOME PAGE
# ============================================================

@app.route("/")
def index():

    return render_template(
        "index.html"
    )


# ============================================================
# RUN LESSON GENERATION (BACKGROUND WORK)
#
# This contains all the heavy work that used to run directly
# inside the /generate-lesson request. It now runs inside a
# background thread so the HTTP request can return immediately
# and avoid Render's platform-level proxy timeout.
# ============================================================

def _run_lesson_generation(
    job_id,
    topic,
    grade,
    subject,
    chapter,
    previous_topic,
    language,
    source_text,
):

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
        # ====================================================

        scenes = split_into_scenes(
            lesson
        )

        if not scenes:

            raise ValueError(
                "No scenes were created from the lesson."
            )

        # ====================================================
        # 3. FIND NEXT SCENE NUMBER
        # ====================================================

        highest_scene_number = 0

        for index, scene in enumerate(scenes):

            if not isinstance(
                scene,
                dict
            ):
                continue

            scene_number = scene.get(
                "scene_number",
                index + 1,
            )

            try:

                scene_number = int(
                    scene_number
                )

            except (
                TypeError,
                ValueError,
            ):

                scene_number = index + 1

            scene["scene_number"] = (
                scene_number
            )

            highest_scene_number = max(
                highest_scene_number,
                scene_number,
            )

        next_scene_number = (
            highest_scene_number + 1
        )

        # ====================================================
        # 4. ADD ASSESSMENT
        # ====================================================

        assessment_scenes, next_scene_number = (
            create_assessment_scenes(
                lesson,
                next_scene_number,
            )
        )

        if assessment_scenes:

            scenes.extend(
                assessment_scenes
            )

        # ====================================================
        # 5. ADD CLOSING
        # ====================================================

        closing_scenes, next_scene_number = (
            create_closing_scene(
                next_scene_number
            )
        )

        scenes.extend(
            closing_scenes
        )

        # ====================================================
        # 6. ASSIGN VISUAL STYLE
        # ====================================================

        assign_visual_style(
            scenes
        )

        # ====================================================
        # 7. CREATE LESSON ID
        # ====================================================

        lesson_uuid = uuid.uuid4().hex

        scene_images = []
        scene_audios = []
        valid_scenes = []

        # ====================================================
        # AUDIO CACHE
        #
        # gTTS only.
        # ====================================================

        audio_cache = {}

        # ====================================================
        # 8. CREATE IMAGES AND AUDIO
        # ====================================================

        for index, scene in enumerate(scenes):

            if not isinstance(
                scene,
                dict
            ):
                continue

            scene_number = scene.get(
                "scene_number",
                index + 1,
            )

            # ------------------------------------------------
            # DETECT VISUAL TYPE
            # ------------------------------------------------

            visual_type = detect_visual_type(
                scene
            )

            if not visual_type:

                visual_type = scene.get(
                    "visual_type",
                    "educational",
                )

            scene["visual_type"] = (
                visual_type
            )

            # ------------------------------------------------
            # CREATE SCENE IMAGE
            # ------------------------------------------------

            image_filename = (
                f"scene_{scene_number}_"
                f"{lesson_uuid}.png"
            )

            image_path = (
                Path(GENERATED_IMAGE_DIR)
                / image_filename
            )

            create_scene_visual(
                scene=scene,
                output_path=image_path,
            )

            scene_images.append(
                image_path
            )

            # ------------------------------------------------
            # GET NARRATION
            # ------------------------------------------------

            scene_text = scene.get(
                "narration",
                "",
            )

            if not scene_text:

                scene_text = scene.get(
                    "text",
                    "",
                )

            if not scene_text:

                scene_text = (
                    f"Scene {scene_number}"
                )

            scene_text = str(
                scene_text
            ).strip()

            # ------------------------------------------------
            # AUDIO FILE
            # ------------------------------------------------

            audio_filename = (
                f"scene_{scene_number}_"
                f"{lesson_uuid}.mp3"
            )

            audio_path = (
                Path(AUDIO_DIR)
                / audio_filename
            )

            pause_seconds = (
                scene.get(
                    "pause_duration",
                    0,
                )
                or 0
            )

            # ------------------------------------------------
            # CACHE KEY
            # ------------------------------------------------

            cache_key = (
                scene_text.strip().lower(),
                language,
                NARRATION_RATE,
                pause_seconds,
            )

            cached_entry = audio_cache.get(
                cache_key
            )

            if cached_entry is not None:

                cached_audio_path, cached_word_boundaries = (
                    cached_entry
                )

                shutil.copyfile(
                    cached_audio_path,
                    audio_path,
                )

                word_boundaries = (
                    cached_word_boundaries
                )

            else:

                # ============================================
                # gTTS ONLY
                # ============================================

                audio_result = generate_audio(
                    text=scene_text,
                    output_path=audio_path,
                    language=language,
                    rate=NARRATION_RATE,
                    pause_seconds=pause_seconds,
                )

                if isinstance(
                    audio_result,
                    dict
                ):

                    word_boundaries = (
                        audio_result.get(
                            "word_boundaries",
                            [],
                        )
                    )

                else:

                    word_boundaries = []

                audio_cache[cache_key] = (
                    audio_path,
                    word_boundaries,
                )

            # ------------------------------------------------
            # SAVE TIMING DATA
            #
            # gTTS does not provide word-level timing.
            # Therefore this remains an empty list.
            # ------------------------------------------------

            scene[
                "speech_word_boundaries"
            ] = word_boundaries

            scene_audios.append(
                audio_path
            )

            valid_scenes.append(
                scene
            )

        # ====================================================
        # 9. VALIDATE GENERATED FILES
        # ====================================================

        if not scene_images:

            raise ValueError(
                "No scene images were generated."
            )

        if not scene_audios:

            raise ValueError(
                "No scene audio files were generated."
            )

        if len(scene_images) != len(scene_audios):

            raise ValueError(
                "The number of scene images and "
                "audio files do not match."
            )

        scenes = valid_scenes

        # ====================================================
        # 10. CREATE COMPLETE VIDEO
        # ====================================================

        video_filename = (
            f"lesson_{lesson_uuid}.mp4"
        )

        video_path = (
            Path(VIDEO_DIR)
            / video_filename
        )

        create_video(
            image_files=scene_images,
            audio_files=scene_audios,
            output_file=video_path,
            scenes=scenes,
        )

        # ====================================================
        # 11. MAIN AUDIO
        # ====================================================

        main_audio_filename = (
            Path(scene_audios[0]).name
            if scene_audios
            else ""
        )

        # ====================================================
        # 12. SAVE LESSON
        # ====================================================

        lesson_id = save_lesson(
            title=lesson.get(
                "title",
                topic,
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
        # 13. IMAGE FILENAMES
        # ====================================================

        scene_image_names = [
            Path(image).name
            for image in scene_images
        ]

        # ====================================================
        # 14. AUDIO FILENAMES
        # ====================================================

        scene_audio_names = [
            Path(audio).name
            for audio in scene_audios
        ]

        # ====================================================
        # 15. STORE RESULT AS DONE
        # ====================================================

        LESSON_JOBS[job_id] = {
            "status": "done",
            "result": {
                "lesson": lesson,
                "lesson_id": lesson_id,
                "audio_filename": main_audio_filename,
                "video_filename": video_filename,
                "scene_images": scene_image_names,
                "scene_audios": scene_audio_names,
            },
        }

    except Exception as error:

        app.logger.exception(
            "Lesson generation failed"
        )

        LESSON_JOBS[job_id] = {
            "status": "error",
            "error": str(error),
        }


# ============================================================
# GENERATE LESSON
#
# Kicks off lesson generation in a background thread and
# returns immediately with a processing page, instead of
# blocking the request for several minutes (which caused
# Render's proxy to return a 502 before gunicorn finished).
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
            url_for("index")
        )

    # ========================================================
    # CREATE JOB AND START BACKGROUND THREAD
    # ========================================================

    job_id = uuid.uuid4().hex

    LESSON_JOBS[job_id] = {
        "status": "processing",
    }

    background_thread = threading.Thread(
        target=_run_lesson_generation,
        args=(
            job_id,
            topic,
            grade,
            subject,
            chapter,
            previous_topic,
            language,
            source_text,
        ),
        daemon=True,
    )

    background_thread.start()

    return render_template(
        "processing.html",
        job_id=job_id,
    )


# ============================================================
# LESSON STATUS (POLLED BY THE PROCESSING PAGE)
# ============================================================

@app.route("/lesson-status/<job_id>")
def lesson_status(job_id):

    job = LESSON_JOBS.get(job_id)

    if not job:

        return jsonify(
            {"status": "not_found"}
        ), 404

    return jsonify(
        {
            "status": job.get("status"),
            "error": job.get("error"),
        }
    )


# ============================================================
# LESSON RESULT (SHOWN ONCE PROCESSING IS DONE)
# ============================================================

@app.route("/lesson-result/<job_id>")
def lesson_result(job_id):

    job = LESSON_JOBS.get(job_id)

    if not job or job.get("status") != "done":

        flash(
            "Lesson not ready yet."
        )

        return redirect(
            url_for("index")
        )

    result = job["result"]

    return render_template(
        "lesson.html",
        lesson=result["lesson"],
        lesson_id=result["lesson_id"],
        audio_filename=result["audio_filename"],
        video_filename=result["video_filename"],
        scene_images=result["scene_images"],
        scene_audios=result["scene_audios"],
    )


# ============================================================
# RUN APPLICATION
# ============================================================

if __name__ == "__main__":

    debug_mode = (
        os.getenv(
            "FLASK_DEBUG",
            "0",
        ).strip()
        == "1"
    )

    port = int(
        os.getenv(
            "PORT",
            "5000",
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=debug_mode,
    )
