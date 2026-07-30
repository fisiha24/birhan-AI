"""
Birhan AI
Lesson Parser

Converts AI-generated lessons into visual teaching scenes.

============================================================
CHANGES IN THIS VERSION
============================================================

This file used to turn each lesson "section" straight into one
explanation scene (spoken_teacher_script + board_text), with no
separation between writing a note and explaining it - the old
"narrator" problem.

It now builds scenes for every phase of the required teaching
model, in order, matching services/ai_engine.py's new schema:

  previous lesson review (unchanged, optional)
  -> greeting
  -> teacher introduction
  -> lesson introduction (subject / chapter / topic)
  -> notebook instruction
  -> ONE SCENE PER NOTE ITEM (progressive board writing -
     "Please write the following title." / "Now write the
     definition." etc., each with its own short board text)
  -> note completion transition ("put your pens down")
  -> ONE EXPLANATION SCENE PER TOPIC, each immediately followed
     by its own ask -> think -> clarify scenes if it has a
     check_question (interactive questions appear DURING the
     explanation, not only at the end)
  -> application activity
  -> summary

The end-of-lesson tiered assessment (Phase 10) is built
separately, in app.py, after these scenes - see
create_assessment_scenes() there.

The old "objectives", "student_check", "quiz", and
"review_questions" driven scenes are removed along with their
JSON fields (services/ai_engine.py no longer generates them);
they were either unused downstream or duplicated content now
covered by lesson_introduction / explanation_sections /
assessment.

============================================================
CHANGE IN THIS VERSION
============================================================

1. "objectives" is back as its own scene/phase (a short
   "students will be able to..." bullet list), placed right
   after the lesson introduction and before notebook
   preparation - reintroduced per an explicit later teaching
   requirement, distinct from lesson_introduction.
2. Each explanation scene now forwards the AI's own
   "visual_suggestion" (when provided) into create_scene(),
   which was already read by services/visual_detector.py's
   scoring - this was previously only populated for a couple
   of fixed scenes (greeting, lesson_introduction), never for
   per-topic explanation scenes.
"""

from config import (

    THINKING_PAUSE_SECONDS,

    APPLAUSE_PAUSE_SECONDS,

)

from services.visual_detector import (

    detect_scene_visuals

)

# ============================================================
# ለቡሌት ነጥቦች የተለያዩ ቀለማት (የዩኒኮድ ምልክቶች)
# ============================================================

BULLET_SYMBOLS = [
    "●",  # ጥቁር ክብ
    "◆",  # አልማዝ
    "▪",  # ትንሽ ካሬ
    "▸",  # የቀኝ ቀስት
    "★",  # ኮከብ
    "❖",  # አልማዝ-ክብ
    "►",  # የቀኝ ሶስት ማዕዘን
    "✧",  # ባዶ ኮከብ
]

# ============================================================
# CLEAN TEXT
# ============================================================

def clean_text(

    value

):

    if value is None:

        return ""

    return str(

        value

    ).strip()

# ============================================================
# NORMALIZE LIST
# ============================================================

def normalize_list(

    value

):

    if value is None:

        return []

    if isinstance(

        value,

        list

    ):

        normalized = []

        for item in value:

            if isinstance(

                item,

                dict

            ):

                item_text = (

                    item.get(

                        "text",

                        ""

                    )

                    or item.get(

                        "content",

                        ""

                    )

                    or item.get(

                        "description",

                        ""

                    )

                    or item.get(

                        "point",

                        ""

                    )

                )

                item_text = clean_text(

                    item_text

                )

            else:

                item_text = clean_text(

                    item

                )

            if item_text:

                normalized.append(

                    item_text

                )

        return normalized

    if isinstance(

        value,

        str

    ):

        value = value.strip()

        if not value:

            return []

        return [

            value

        ]

    return []

# ============================================================
# GET FIRST AVAILABLE TEXT
# ============================================================

def get_first_text(

    data,

    keys

):

    if not isinstance(

        data,

        dict

    ):

        return ""

    for key in keys:

        value = clean_text(

            data.get(

                key,

                ""

            )

        )

        if value:

            return value

    return ""

# ============================================================
# CREATE SCENE
# ============================================================

def create_scene(

    scene_type,

    title,

    narration,

    board_text="",

    visual_suggestion="",

    visual_query="",

    visual_type="educational",

    key_points=None,

    example="",

    check_question="",

    pause_duration=0

):

    scene = {

        "scene_number": 0,

        "type": clean_text(

            scene_type

        ),

        "title": clean_text(

            title

        ),

        "narration": clean_text(

            narration

        ),

        "board_text": clean_text(

            board_text

        ),

        "visual_suggestion": clean_text(

            visual_suggestion

        ),

        "visual_query": clean_text(

            visual_query

        ),

        "visual_type": clean_text(

            visual_type

        ) or "educational",

        "key_points": key_points or [],

        "example": clean_text(

            example

        ),

        "check_question": clean_text(

            check_question

        )

    }

    if pause_duration:

        scene["pause_duration"] = pause_duration

    return scene

# ============================================================
# FORMAT BULLET POINTS
# ============================================================

def format_bullet_points(items, use_symbols=True):
    """
    ቡሌት ነጥቦችን በአቀባዊ እና በተለያዩ ምልክቶች ያስቀምጣል
    """
    if not items:
        return ""

    formatted_lines = []
    for index, item in enumerate(items):
        item = clean_text(item)
        if not item:
            continue

        if use_symbols:
            symbol = BULLET_SYMBOLS[index % len(BULLET_SYMBOLS)]
            formatted_lines.append(f"{symbol} {item}")
        else:
            formatted_lines.append(f"• {item}")

    return "\n".join(formatted_lines)


# ============================================================
# INTERACTIVE QUESTION HELPER
#
# Builds the ask -> think -> clarify scene triple used both
# for each explanation section's in-the-moment check question
# (Phase 7 / Phase "Interactive Questions" + "Thinking Time" +
# "Clarification"). Mirrors the ask/think/answer pattern used
# for the end-of-lesson assessment in app.py, so the same
# familiar rhythm is used everywhere a question is asked.
# ============================================================

def _build_check_question_scenes(question, clarification):

    question = clean_text(question)

    clarification = clean_text(clarification)

    if not question:

        return []

    scenes = []

    ask_narration = "Let's check your understanding. " + question

    scenes.append(

        create_scene(

            scene_type="check_question",

            title="Quick Check",

            narration=ask_narration,

            board_text=question,

            visual_suggestion="A quick comprehension check",

            visual_type="educational",

        )

    )

    scenes.append(

        create_scene(

            scene_type="thinking_pause",

            title="Think About Your Answer",

            narration=(

                "Take a moment to think about your answer."

                f" [PAUSE:{THINKING_PAUSE_SECONDS}]"

            ),

            board_text="Think about your answer...",

            visual_type="educational",

            pause_duration=THINKING_PAUSE_SECONDS,

        )

    )

    if clarification:

        clarify_narration = clarification

        scenes.append(

            create_scene(

                scene_type="clarification",

                title="Let's Check Together",

                narration=clarify_narration,

                board_text=clarification,

                visual_type="educational",

            )

        )

    return scenes


# ============================================================
# PARSE LESSON
# ============================================================

def parse_lesson(

    lesson

):

    scenes = []

    if not isinstance(

        lesson,

        dict

    ):

        return scenes

    # ========================================================
    # LESSON TITLE / SUBJECT / CHAPTER
    # ========================================================

    lesson_title = clean_text(

        lesson.get(

            "title",

            ""

        )

    )

    lesson_subject = clean_text(

        lesson.get(

            "subject",

            ""

        )

    )

    lesson_chapter = clean_text(

        lesson.get(

            "chapter",

            ""

        )

    )

    # ========================================================
    # PHASE 0: PREVIOUS LESSON REVIEW ("Review Box")
    #
    # Required to be the very first thing on screen, so
    # students are quickly reminded of the previous lesson
    # before today's topic starts. Only created when the
    # lesson actually has review content (i.e. a previous
    # topic was supplied when the lesson was generated).
    # ========================================================

    previous_lesson_review = get_first_text(

        lesson,

        [

            "previous_lesson_review",

        ]

    )

    if previous_lesson_review:

        scenes.append(

            create_scene(

                scene_type="previous_lesson_review",

                title="Previous Lesson Review",

                narration=previous_lesson_review,

                board_text=(

                    "REVIEW: PREVIOUS LESSON\n"

                    + previous_lesson_review

                ),

                visual_suggestion=(

                    "A quick classroom recap of the "

                    "previous lesson"

                ),

                visual_type="review",

            )

        )

    # ========================================================
    # PHASE 1a: GREETING
    # ========================================================

    greeting = get_first_text(

        lesson,

        [

            "greeting",

        ]

    )

    if greeting:

        scenes.append(

            create_scene(

                scene_type="greeting",

                title="Welcome",

                narration=greeting,

                board_text=greeting,

                visual_suggestion="A friendly classroom opening",

                visual_type="classroom"

            )

        )

    # ========================================================
    # PHASE 1b: TEACHER INTRODUCTION
    # ========================================================

    teacher_introduction = get_first_text(

        lesson,

        [

            "teacher_introduction",

        ]

    )

    if teacher_introduction:

        scenes.append(

            create_scene(

                scene_type="teacher_introduction",

                title="Your Teacher",

                narration=teacher_introduction,

                board_text=teacher_introduction,

                visual_suggestion="The teacher introducing themself",

                visual_type="classroom"

            )

        )

    # ========================================================
    # PHASE 1c: SUBJECT / CHAPTER / LESSON INTRODUCTION
    # ========================================================

    lesson_introduction = get_first_text(

        lesson,

        [

            "lesson_introduction",

        ]

    )

    if lesson_introduction or lesson_subject or lesson_chapter or lesson_title:

        metadata_lines = []

        if lesson_subject:

            metadata_lines.append("Subject: " + lesson_subject)

        if lesson_chapter:

            metadata_lines.append("Chapter: " + lesson_chapter)

        if lesson_title:

            metadata_lines.append("Lesson: " + lesson_title)

        narration = lesson_introduction or (

            "Today's lesson is " + lesson_title + "."

        )

        # VERBATIM BOARD RULE: only show metadata lines on the
        # board that are also words appearing in the narration,
        # otherwise fall back to a short excerpt of the
        # narration itself so the board sync never suppresses
        # this scene entirely.

        board_text = (

            "\n".join(metadata_lines)

            if lesson_introduction

            else narration

        )

        scenes.append(

            create_scene(

                scene_type="lesson_introduction",

                title="Today's Lesson",

                narration=narration,

                board_text=board_text or narration,

                visual_suggestion=(

                    "A title card showing the subject, "

                    "chapter, and lesson title"

                ),

                visual_type="title",

            )

        )

    # ========================================================
    # PHASE 1d: LEARNING OBJECTIVES
    #
    # Reintroduced as its own distinct phase (a short "students
    # will be able to..." list), shown right after the lesson
    # introduction and before notebook preparation, per the
    # required teaching order.
    # ========================================================

    objectives = normalize_list(

        lesson.get("objectives", [])

    )

    if objectives:

        board_text = format_bullet_points(objectives)

        narration = (

            "By the end of this lesson, you should be able to: "

            + ". ".join(objectives)

            + "."

        )

        scenes.append(

            create_scene(

                scene_type="objectives",

                title="Learning Objectives",

                narration=narration,

                board_text=board_text,

                visual_suggestion="A clear presentation of the learning objectives",

                visual_type="objectives",

                key_points=objectives,

            )

        )

    # ========================================================
    # PHASE 2a: NOTEBOOK PREPARATION
    # ========================================================

    notebook_instruction = get_first_text(

        lesson,

        [

            "notebook_instruction",

        ]

    )

    if notebook_instruction:

        scenes.append(

            create_scene(

                scene_type="notebook_instruction",

                title="Notebook Preparation",

                narration=notebook_instruction,

                board_text=notebook_instruction,

                visual_suggestion="Students preparing notebooks and pens",

                visual_type="classroom",

            )

        )

    # ========================================================
    # PHASE 2b/3: TEACHER-LED NOTE-TAKING
    # PROGRESSIVE DIGITAL BOARD WRITING
    #
    # One scene per note item. The teacher's spoken instruction
    # PLUS the note text itself are both spoken (so the board
    # sync has real words to match), and the board only ever
    # shows that single note item - never the whole lesson at
    # once.
    # ========================================================

    note_sections = lesson.get("note_sections", [])

    if isinstance(note_sections, list):

        for note_item in note_sections:

            if not isinstance(note_item, dict):

                continue

            instruction = clean_text(note_item.get("instruction", ""))

            note_text = clean_text(note_item.get("note_text", ""))

            if not note_text:

                continue

            if not instruction:

                instruction = "Please write the following note."

            narration = instruction + " " + note_text

            scenes.append(

                create_scene(

                    scene_type="note_taking",

                    title="Write This Down",

                    narration=narration,

                    board_text=note_text,

                    visual_suggestion="The teacher writing on the board",

                    visual_type="educational",

                )

            )

    # ========================================================
    # PHASE 4: NOTE COMPLETION
    #
    # Required transition BEFORE detailed explanation begins.
    # ========================================================

    note_completion = get_first_text(

        lesson,

        [

            "note_completion",

        ]

    )

    if note_completion:

        scenes.append(

            create_scene(

                scene_type="note_completion",

                title="Note Complete",

                narration=note_completion,

                board_text=note_completion,

                visual_suggestion="Students finishing writing their notes",

                visual_type="classroom",

            )

        )

    # ========================================================
    # PHASE 5-7: DETAILED EXPLANATION, EXAMPLES,
    # INTERACTIVE QUESTIONS, THINKING TIME, CLARIFICATION
    #
    # One explanation scene per topic, immediately followed by
    # its own ask -> think -> clarify triple if it has a check
    # question - so questions appear DURING the explanation,
    # not only bunched up at the very end.
    # ========================================================

    explanation_sections = lesson.get("explanation_sections", [])

    if isinstance(explanation_sections, list):

        for section in explanation_sections:

            if not isinstance(section, dict):

                continue

            section_title = clean_text(section.get("title", "")) or "Explanation"

            narration = clean_text(section.get("spoken_teacher_script", ""))

            board_text = clean_text(section.get("board_text", ""))

            example = clean_text(section.get("example", ""))

            check_question = clean_text(section.get("check_question", ""))

            clarification = clean_text(section.get("clarification", ""))

            visual_suggestion = clean_text(section.get("visual_suggestion", ""))

            if not narration and not board_text:

                continue

            if not board_text:

                # Fall back to the section title so the board
                # sync always has at least something that
                # matches the spoken narration.

                board_text = section_title

            scenes.append(

                create_scene(

                    scene_type="explanation",

                    title=section_title,

                    narration=narration,

                    board_text=board_text,

                    visual_suggestion=visual_suggestion,

                    visual_type="educational",

                    example=example,

                    check_question=check_question,

                )

            )

            scenes.extend(

                _build_check_question_scenes(

                    check_question,

                    clarification,

                )

            )

    # ========================================================
    # PHASE 8: APPLICATION
    # ========================================================

    application_activity = get_first_text(

        lesson,

        [

            "application_activity",

        ]

    )

    if application_activity:

        scenes.append(

            create_scene(

                scene_type="application",

                title="Try It Yourself",

                narration=application_activity,

                board_text=application_activity,

                visual_suggestion="Students applying what they learned",

                visual_type="educational",

            )

        )

    # ========================================================
    # PHASE 9: SUMMARY
    # ========================================================

    summary = get_first_text(

        lesson,

        [

            "summary",

        ]

    )

    if summary:

        scenes.append(

            create_scene(

                scene_type="summary",

                title="Summary",

                narration=summary,

                board_text=summary,

                visual_suggestion="A visual review of the main lesson ideas",

                visual_type="summary"

            )

        )

    # ========================================================
    # NOTE: PHASE 10 (tiered assessment) is intentionally NOT
    # built here. It is built in app.py's
    # create_assessment_scenes(), which reads lesson["assessment"]
    # (easy / moderate / difficult) after these scenes, and
    # continues the scene numbering sequentially from here.
    # ========================================================

    # ========================================================
    # ASSIGN SCENE NUMBERS
    # ========================================================

    for index, scene in enumerate(

        scenes

    ):

        scene["scene_number"] = index + 1

    # ========================================================
    # DETECT VISUAL TYPES
    # ========================================================

    if scenes:

        detected_scenes = detect_scene_visuals(

            scenes

        )

        if isinstance(

            detected_scenes,

            list

        ):

            scenes = detected_scenes

    # ========================================================
    # REASSIGN SCENE NUMBERS
    # ========================================================

    for index, scene in enumerate(

        scenes

    ):

        if isinstance(

            scene,

            dict

        ):

            scene["scene_number"] = index + 1

    return scenes

# ============================================================
# PUBLIC FUNCTION
# ============================================================

def split_into_scenes(

    lesson

):

    return parse_lesson(

        lesson

    )