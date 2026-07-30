"""
Birhan AI
AI Engine

Generates classroom lessons and answers student questions.

============================================================
CHANGES IN THIS VERSION
============================================================

This file used to generate a lesson as a flat list of
"sections", each with one long spoken_teacher_script and one
board_text - the AI was effectively asked to write narration
for a narrator to read, not a teacher to teach.

Per the required teaching model (greeting -> teacher intro ->
subject/lesson intro -> notebook preparation -> teacher-led
note-taking with progressive board writing -> note completion
-> detailed explanation -> examples -> interactive questions
-> thinking time -> application -> summary -> tiered
assessment), the JSON schema and prompt below have been
rewritten so the AI returns STRUCTURED data for every one of
those phases separately, instead of one long paragraph:

1. "teacher_introduction" - the teacher naming themself and
   the subject they teach, BEFORE the lesson content starts.
2. "lesson_introduction" - subject / unit / sub-unit framing,
   separate from the teacher introduction.
3. "notebook_instruction" - the explicit "take out your
   notebooks and pens" instruction, required before any
   note-taking begins.
4. "note_sections" - the actual note the students write,
   broken into small, separately-revealed items (a title, a
   heading, a definition, a key point, ...). Each item carries
   its own short spoken instruction ("Please write the
   following title.") AND the exact note text, so the board
   updates progressively, one note item at a time, instead of
   the whole lesson appearing at once.
5. "note_completion" - the required transition line ("We have
   finished writing the note. Please put your pens down.")
   that must come BEFORE detailed explanation starts.
6. "explanation_sections" - the detailed explanation of the
   note, one entry per note topic, each following the
   required Explain -> Example -> Question -> (pause) ->
   Clarify pattern via "spoken_teacher_script", "example",
   "check_question", and "clarification".
7. "application_activity" - a concrete, practical activity
   for students to do with what they just learned.
8. "assessment" - tiered "easy" / "moderate" / "difficult"
   questions, generated from what was ACTUALLY taught, instead
   of a flat, uncapped list of "review_questions" bolted on
   separately. Every item has a real, specific "answer", so
   the video never has to fall back to a generic placeholder
   sentence.

Fields that were never actually used downstream, or that
duplicated other fields and invited repeated/duplicate content
("objectives", "student_check", "quiz", "review_questions",
per-section "assessment") have been removed.

VERBATIM PHRASE RULE (kept, and now applied lesson-wide): the
video's word-level board sync (services/video_generator.py)
can only reveal a board word at the moment it is actually
spoken, and it deliberately SUPPRESSES any board content that
never appears in the narration at all. So every board_text /
note_text this file asks for must be a short phrase that is
copied verbatim from its own scene's spoken text - not
paraphrased - or it will silently fail to appear on the board.
This applies to a scene's OWN spoken text; it does not mean
copying sentences from the source material (see below).

============================================================
CHANGE IN THIS VERSION: SOURCE MATERIAL HANDLING
============================================================

Previously, when a long textbook/curriculum "source_text" was
supplied, it was simply appended to the user prompt with no
instruction on how to use it - so the model's easiest path was
to compress it and read the compressed version aloud, which is
exactly the "long textbook -> shorten -> read" behavior this
system is not supposed to produce.

The system prompt now explicitly instructs the model to treat
source material as a KNOWLEDGE SOURCE to study and understand
first (central concept, subtopics, relationships, likely
difficulties, real-life examples, misconceptions, activities,
check questions), and to then design the lesson - and write
"note_text" - in its own words, never by copying or lightly
trimming source sentences. This is a prompt-only change: the
JSON schema, scene structure, and every other file in the
pipeline are unchanged.

============================================================
CHANGE IN THIS VERSION: OBJECTIVES + VISUAL AIDS
============================================================

Two additions requested afterward:

- "objectives" is back as its own top-level field (a short list
  of 3-6 concrete "students will be able to..." statements),
  shown as its own phase right after the lesson introduction
  and before notebook preparation. (A JSON field with this
  name existed in an even earlier version of this file and was
  removed for being unused/duplicative at the time - it is
  reintroduced here as its own distinct teaching phase, per an
  explicit later requirement.)
- Each explanation_sections entry can now carry an optional
  "visual_suggestion" - a short, concrete description of a
  diagram/illustration for that specific concept, filled in
  ONLY when a visual genuinely helps. services/lesson_parser.py
  already threads a scene's "visual_suggestion" field into
  services/visual_detector.py's scoring, so this makes the
  AI's own judgment about when a visual helps part of what
  drives the existing keyword-based visual-type detection,
  instead of relying on keyword matches alone.
"""

import json
import re
import threading
import requests

from config import (
    AI_API_KEYS,
    AI_BASE_URL,
    AI_MODEL,
    AI_MAX_TOKENS,
    AI_TPM_LIMIT,
)

# ============================================================
# EXACT REQUIRED CLOSING PHRASE
# ============================================================

REQUIRED_CLOSING_PHRASE = (
    "I am Teacher BIRHANU THANK YOU TEACHER MY CREATER Fisiha Melkie, "
    "and thank you for being with me!"
)

# ============================================================
# ASSESSMENT SIZE (PER TIER)
#
# Phase 10 requires three difficulty tiers (easy / moderate /
# difficult), generated from what was actually taught. Raised
# from the original 3/3/2 to better match a fuller, longer
# lesson - every item still needs a real, specific answer (see
# app.py's create_assessment_scenes(), which turns each of
# these into an ask -> think -> answer(+applause) scene), so
# these are still deliberately not huge numbers.
# ============================================================

EASY_QUESTION_COUNT = 5

MODERATE_QUESTION_COUNT = 4

DIFFICULT_QUESTION_COUNT = 3


# ============================================================
# NOTE / EXPLANATION SIZE GUIDANCE
#
# Not a hard cap enforced in code (the AI decides how many note
# items a given topic actually needs) - just guidance given in
# the prompt. Raised from the original 3-10 range: that range
# was tuned for a short, topic-only lesson and was actively
# forcing a long, multi-subtopic source document (e.g. a full
# textbook chapter) to be compressed down into far fewer note
# items than the material actually contains, which is exactly
# the "shortened instead of long and thorough" complaint this
# range is meant to fix. The prompt below also explicitly ties
# the item count to how much distinct material the source (or
# topic) actually has, instead of treating this range as a
# target to hit regardless of source length.
# ============================================================

MIN_NOTE_ITEMS = 4

MAX_NOTE_ITEMS = 24


# ============================================================
# TPM-AWARE REQUEST SIZING
#
# The API's tokens-per-minute limit (AI_TPM_LIMIT, config.py)
# covers the PROMPT and the requested completion COMBINED. A
# fixed max_tokens (the previous approach) ignores how big the
# prompt itself is - a long source_text plus this file's own
# long system prompt can already use most of the budget, so
# requesting the full AI_MAX_TOKENS ceiling on top pushes the
# total over the limit and the API rejects the request outright
# (HTTP 413, "rate_limit_exceeded") instead of just returning a
# shorter answer.
#
# No tokenizer library is assumed to be installed, so token
# counts are estimated from character length. 3 characters per
# token is used (rather than the more common ~4 for plain
# English) to stay conservative: it slightly overestimates
# token usage, which is the safe direction here, and Amharic
# source/lesson text in particular tends to use MORE tokens per
# character than English under most BPE tokenizers, so a single
# conservative ratio is used for both languages rather than
# risking an under-estimate for Amharic content.
# ============================================================

CHARS_PER_TOKEN_ESTIMATE = 2

# Tokens deliberately left unused as a safety margin, since the
# character-based estimate is approximate, not exact.
RATE_LIMIT_SAFETY_MARGIN = 1000

# Below this, there isn't enough token budget left to generate a
# real structured lesson (greeting through assessment) - better
# to fail with a clear, actionable message than to send a
# request that will either be rejected or come back as
# truncated, invalid JSON.
MIN_COMPLETION_TOKENS = 1500


def _estimate_tokens(text):

    if not text:
        return 0

    return max(1, len(str(text)) // CHARS_PER_TOKEN_ESTIMATE)


def _is_rate_limit_error(error_message):

    error_message = str(error_message).lower()

    return (
        "rate_limit_exceeded" in error_message
        or "tokens per minute" in error_message
        or " 413" in f" {error_message}"
    )


# ============================================================
# API KEY CHECK
# ============================================================

def _check_api_keys():
    if not AI_API_KEYS:
        raise ValueError(
            "No Groq API key is configured. Set GROQ_API_KEY (a single "
            "key) or GROQ_API_KEYS (a comma-separated list of keys) in "
            "the .env file."
        )


# ============================================================
# CALL AI (SINGLE KEY)
# ============================================================

def _call_ai(
    messages,
    api_key,
    temperature=0.2,
    json_mode=False,
    max_tokens=None,
):
    url = f"{AI_BASE_URL.rstrip('/')}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "temperature": temperature,
    }

    if json_mode:
        payload["response_format"] = {
            "type": "json_object"
        }

    if max_tokens:
        # Without this, the API falls back to its own default
        # completion-length cap, which silently truncates a long,
        # thorough lesson before it can finish - see AI_MAX_TOKENS
        # in config.py for why this matters.
        payload["max_tokens"] = max_tokens

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=300,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"AI API Error {response.status_code}: {response.text}"
        )

    data = response.json()

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(
            f"Invalid AI response: {error}"
        )


# ============================================================
# KEY ROTATION
#
# Each configured API key has its own separate tokens-per-minute
# budget on Groq's side. A module-level rotation pointer (guarded
# by a lock, since Flask can serve requests on multiple threads)
# advances on every call so consecutive lesson generations start
# from a different key instead of always hammering the first one
# in the list.
# ============================================================

_key_rotation_lock = threading.Lock()

_key_rotation_index = 0


def _next_key_rotation_order():

    """
    Returns AI_API_KEYS reordered to start from the next rotation
    position, wrapping around - e.g. with 3 keys, consecutive
    calls see [A, B, C], then [B, C, A], then [C, A, B], ...
    """

    global _key_rotation_index

    if not AI_API_KEYS:
        return []

    with _key_rotation_lock:

        start = _key_rotation_index % len(AI_API_KEYS)

        _key_rotation_index += 1

    return [
        AI_API_KEYS[(start + offset) % len(AI_API_KEYS)]
        for offset in range(len(AI_API_KEYS))
    ]


def _call_ai_with_rotation(
    messages,
    temperature=0.2,
    json_mode=False,
    max_tokens=None,
):
    """
    Try every API key loaded from GROQ_API_KEYS.

    GROQ_API_KEYS is a comma-separated list with no fixed number of keys.
    Example:
        GROQ_API_KEYS=key1,key2,key3,...,key15,...

    If a key is rate-limited, the next key is tried. If all keys are
    rate-limited, the completion size is reduced and the keys are tried
    again with a short backoff.
    """

    _check_api_keys()

    ordered_keys = _next_key_rotation_order()

    retry_sizes = []

    if max_tokens:
        for fraction in (1.0, 0.70, 0.50, 0.35):
            candidate = max(
                MIN_COMPLETION_TOKENS,
                int(max_tokens * fraction),
            )
            if candidate not in retry_sizes:
                retry_sizes.append(candidate)
    else:
        retry_sizes = [None]

    last_error = None

    # First pass: use every configured key once.
    for api_key in ordered_keys:
        try:
            return _call_ai(
                messages=messages,
                api_key=api_key,
                temperature=temperature,
                json_mode=json_mode,
                max_tokens=retry_sizes[0] if retry_sizes else max_tokens,
            )
        except RuntimeError as error:
            last_error = error

            if not _is_rate_limit_error(error):
                raise

            continue

    # All keys rejected the first request size.
    # Retry smaller completions, rotating through all loaded keys again.
    import time

    for retry_index, reduced_max_tokens in enumerate(retry_sizes[1:], start=1):
        time.sleep(min(5 * retry_index, 15))

        retry_keys = _next_key_rotation_order()

        for api_key in retry_keys:
            try:
                return _call_ai(
                    messages=messages,
                    api_key=api_key,
                    temperature=temperature,
                    json_mode=json_mode,
                    max_tokens=reduced_max_tokens,
                )
            except RuntimeError as error:
                last_error = error

                if not _is_rate_limit_error(error):
                    raise

                continue

    key_count = len(AI_API_KEYS)

    raise RuntimeError(
        f"Groq rate-limited all {key_count} configured API keys for "
        f"this request (TPM limit {AI_TPM_LIMIT} per key). The request "
        "was retried with smaller completion sizes. Shorten the "
        "'Additional Information' source text or wait for the TPM "
        "window to reset and try again."
    ) from last_error


# ============================================================
# CLEAN JSON
# ============================================================

def _clean_json_response(content):
    if not content:
        return ""

    content = content.strip()
    content = re.sub(r"^```json\s*", "", content, flags=re.IGNORECASE)
    content = re.sub(r"^```\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    return content.strip()


# ============================================================
# SCHEMA HELPERS - VALIDATE / NORMALIZE THE AI'S RESPONSE
# ============================================================

def _as_text(value):

    if value is None:
        return ""

    return str(value).strip()


def _as_list(value):

    if isinstance(value, list):
        return value

    return []


def _normalize_note_sections(raw_sections):

    normalized = []

    for item in _as_list(raw_sections):

        if not isinstance(item, dict):
            continue

        instruction = _as_text(item.get("instruction"))

        note_text = _as_text(item.get("note_text"))

        if not note_text:
            continue

        if not instruction:
            instruction = "Please write the following note."

        normalized.append(
            {
                "instruction": instruction,
                "note_text": note_text,
            }
        )

    return normalized


def _normalize_objectives(raw_objectives):

    normalized = []

    for item in _as_list(raw_objectives):

        text = _as_text(item)

        if text:

            normalized.append(text)

    return normalized


def _normalize_explanation_sections(raw_sections):

    normalized = []

    for item in _as_list(raw_sections):

        if not isinstance(item, dict):
            continue

        title = _as_text(item.get("title"))

        spoken_teacher_script = _as_text(
            item.get("spoken_teacher_script")
        )

        if not title and not spoken_teacher_script:
            continue

        normalized.append(
            {
                "title": title or "Explanation",
                "spoken_teacher_script": spoken_teacher_script,
                "board_text": _as_text(item.get("board_text")),
                "example": _as_text(item.get("example")),
                "check_question": _as_text(item.get("check_question")),
                "clarification": _as_text(item.get("clarification")),
                "visual_suggestion": _as_text(item.get("visual_suggestion")),
            }
        )

    return normalized


def _normalize_assessment_items(raw_items):

    normalized = []

    for item in _as_list(raw_items):

        if not isinstance(item, dict):
            continue

        question = _as_text(item.get("question"))

        answer = _as_text(item.get("answer"))

        if not question or not answer:

            # Per the "no generic placeholder answers" rule,
            # an assessment item without a real question AND
            # a real answer is dropped entirely rather than
            # padded out downstream.

            continue

        options = item.get("options", [])

        if not isinstance(options, list):
            options = []

        options = [
            _as_text(option)
            for option in options
            if _as_text(option)
        ]

        normalized.append(
            {
                "question": question,
                "options": options,
                "answer": answer,
                "explanation": _as_text(item.get("explanation")),
            }
        )

    return normalized


def _normalize_assessment(raw_assessment):

    if not isinstance(raw_assessment, dict):
        raw_assessment = {}

    return {
        "easy": _normalize_assessment_items(
            raw_assessment.get("easy", [])
        )[:EASY_QUESTION_COUNT],

        "moderate": _normalize_assessment_items(
            raw_assessment.get("moderate", [])
        )[:MODERATE_QUESTION_COUNT],

        "difficult": _normalize_assessment_items(
            raw_assessment.get("difficult", [])
        )[:DIFFICULT_QUESTION_COUNT],
    }


# ============================================================
# GENERATE LESSON
# ============================================================

def generate_lesson(
    topic,
    grade="Grade 7",
    subject="General Science",
    language="English",
    source_text="",
    chapter="",
    previous_topic="",
):

    previous_lesson_instruction = ""

    if previous_topic:
        previous_lesson_instruction = f"""
==================================================
PREVIOUS LESSON REVIEW (REQUIRED)
==================================================

The students' previous lesson was about:
{previous_topic}

Fill "previous_lesson_review" with a SHORT, spoken-style
recap (2-4 sentences) that quickly reminds students what
they learned in that previous lesson, AND ends with one
bridging sentence that clearly connects that recap to
TODAY'S topic.
"""

    system_prompt = f"""
You are Birhan AI, an expert classroom teacher for Ethiopian students.
You are Teacher Birhan.
Your lesson will be converted into an educational teaching video where a
digital classroom board is written on progressively while you speak.

============================================================
YOU MUST BEHAVE LIKE A REAL CLASSROOM TEACHER, NOT A NARRATOR
============================================================

Do NOT write one long paragraph that explains everything at once.
You must return SEPARATE, STRUCTURED content for every phase of a real
lesson, in this exact order:

1. GREETING - a short, warm classroom greeting only ("Good morning,
   students.").
2. TEACHER INTRODUCTION - you introduce YOURSELF and the subject you
   teach ("My name is Birhan. I am your {subject} teacher.").
3. LESSON INTRODUCTION - introduce today's subject, chapter/unit, and
   the specific topic, so students know exactly what is being studied
   today.
4. LEARNING OBJECTIVES ("objectives") - 3-6 short, concrete statements
   of what students will be able to do by the end of the lesson (e.g.
   "explain what a measurement is", "name three indigenous units of
   length"). Each one a single short sentence, written as a list.
5. NOTEBOOK INSTRUCTION - explicitly tell students to take out their
   notebooks and pens, and briefly explain that you will write a note
   together before explaining it.
6. NOTE-TAKING WITH STUDENTS ("note_sections") - guide students to
   write a SHORT, ORGANIZED note, ONE SMALL PIECE AT A TIME (a title,
   then a heading, then a definition, then a key point, and so on).
   For EACH piece: give a short spoken instruction ("Please write the
   following title.", "Now write the definition.", "Now write this
   important point.") AND the exact note text itself. Note text must
   be short (a title, a heading, a one-sentence definition, or a short
   formula/fact) - never a paragraph.
7. NOTE COMPLETION - a short transition AFTER all notes are written,
   telling students the note is finished and to put their pens down,
   because you are now going to explain it in detail. This must come
   BEFORE any detailed explanation.
8. DETAILED EXPLANATION ("explanation_sections") - one entry per note
   topic above. Each entry must follow this pattern in its spoken
   text: EXPLAIN the idea in your own words -> give a concrete EXAMPLE
   -> ask an interactive CHECK QUESTION about only what has already
   been taught -> (the video will insert a thinking pause here) ->
   CLARIFY the correct answer in the "clarification" field. Do not ask
   about anything not yet explained. Where relevant to the topic,
   correct a common misconception and connect the idea to students'
   daily life as a natural part of the spoken explanation. When a
   concept is genuinely difficult, use a simple analogy, comparison,
   logical rule, or memory trick to make it stick - but never make an
   easy idea artificially complicated by forcing one in.
   VISUAL AID: if this specific concept would be easier to understand
   with a picture or diagram (e.g. a labeled diagram, a process cycle,
   a comparison chart, a real object), fill "visual_suggestion" with a
   short, concrete description of exactly what that image should show
   (e.g. "a labeled diagram of a hand-span, digit, and cubit being
   measured on a human arm"). Leave "visual_suggestion" empty for
   concepts that don't need one - do not invent a visual for every
   entry.
9. APPLICATION - one simple, practical activity students can do with
   what they just learned (e.g. measuring something in their own
   classroom).
10. SUMMARY - a concise recap of only the main points already taught
   (not a re-explanation).
11. ASSESSMENT - tiered questions generated ONLY from what was
    actually taught in this lesson:
    - "easy": {EASY_QUESTION_COUNT} recall/definition questions.
    - "moderate": {MODERATE_QUESTION_COUNT} explanation/comparison
      questions.
    - "difficult": {DIFFICULT_QUESTION_COUNT} application/reasoning
      questions (e.g. a short scenario to explain).
    EVERY assessment question must have a real, specific "answer" and
    a short "explanation" - never leave these blank. Multiple-choice
    items should include 3-4 "options"; open-ended items should leave
    "options" empty.

============================================================
HOW TO USE SOURCE MATERIAL (WHEN PROVIDED BELOW)
============================================================

If source material (a textbook or curriculum passage) is provided in
the user message, it is a SOURCE OF KNOWLEDGE for you to study - it
is NOT a ready-made speaking script. Never shorten it and read the
shortened version aloud, and never build the lesson by following it
paragraph by paragraph.

DO NOT COMPRESS A LONG SOURCE INTO A SHORT LESSON:
A long, dense source (e.g. a full textbook chapter with several
distinct subtopics) must produce a LONG, THOROUGH lesson - as deep
and complete as a detailed study guide, not a brief overview. Do not
remove an important idea, definition, classification, or example
merely because the source text is long. If the source covers several
distinct subtopics (for example: several named indigenous measuring
units, several fundamental quantities, several derived quantities, a
comparison of concepts, etc.), EVERY one of those subtopics gets its
OWN note item and its OWN explanation section - do not merge several
distinct named things into one vague note item just to keep the count
low. A rich source should produce MORE note items and MORE
explanation sections than a short topic would, not the same small
number regardless of source length.

Before writing anything, study the complete source material and
identify:
- the central concept of the lesson;
- the important subtopics and how they relate to each other;
- concepts students are likely to find difficult;
- useful real-life examples;
- misconceptions students commonly have;
- a suitable, simple classroom activity;
- appropriate questions for checking understanding.

Then design the lesson in the best logical, pedagogical order for
students - which is not necessarily the order the source presents
things in.

NOTE_TEXT MUST BE REWRITTEN, NEVER COPIED:
Do not copy a source sentence into "note_text", and do not produce a
note by deleting a few words from a longer source sentence. Fully
understand the underlying idea first, then write it again yourself,
in short, clear, original words. A single note item must be something
a student can write in a few seconds - a title, a heading, a
one-sentence definition, or a short fact/formula/example value -
never a shortened textbook paragraph. Being SHORT PER ITEM and
covering MANY items are not in conflict - keep each individual note
item brief, but do not reduce the NUMBER of note items just because
each one is short.

EXPLANATION MUST GO BEYOND THE NOTE, AND MUST BE SUBSTANTIAL:
The "spoken_teacher_script" in each explanation_sections entry must
add real teaching value that the note itself does not contain - a
worked example, a comparison, a common misconception and its
correction, or a connection to students' daily life - in your own
natural teacher language. Write several full sentences of genuine
teaching for each entry, not one short line. Never simply read the
note back to students and move on.

NO REPEATED PHRASES:
Never repeat the same sentence, greeting, instruction, or question
twice, anywhere in the lesson.

NOTE-TAKING VS EXPLANATION MUST STAY SEPARATE:
Do not explain a concept while students are still writing the note for
it. All note items come first; the note_completion transition comes
next; only then does explanation begin.

VERBATIM BOARD RULE (CRITICAL):
Every "note_text" and every "board_text" must be made of words that
are copied EXACTLY (verbatim) from that same item's own spoken text
(the instruction for note items, or spoken_teacher_script for
explanation items). Board text must be short educational notes only
(key terms, short definitions, formulas, facts) - never a full
paragraph, and never text that isn't also spoken somewhere in that
same scene.

{previous_lesson_instruction}

RETURN ONLY VALID JSON WITH THIS EXACT STRUCTURE:
{{
  "title": "",
  "subject": "",
  "chapter": "",
  "grade": "",
  "language": "",
  "previous_lesson_review": "",
  "greeting": "",
  "teacher_introduction": "",
  "lesson_introduction": "",
  "objectives": [],
  "notebook_instruction": "",
  "note_sections": [
    {{
      "instruction": "",
      "note_text": ""
    }}
  ],
  "note_completion": "",
  "explanation_sections": [
    {{
      "title": "",
      "spoken_teacher_script": "",
      "board_text": "",
      "example": "",
      "check_question": "",
      "clarification": "",
      "visual_suggestion": ""
    }}
  ],
  "application_activity": "",
  "summary": "",
  "assessment": {{
    "easy": [
      {{
        "question": "",
        "options": [],
        "answer": "",
        "explanation": ""
      }}
    ],
    "moderate": [],
    "difficult": []
  }},
  "closing_phrase": "{REQUIRED_CLOSING_PHRASE}"
}}

Include between {MIN_NOTE_ITEMS} and {MAX_NOTE_ITEMS} items in
"note_sections", with one matching entry in "explanation_sections" per
major note topic (a definition and a related key point may share one
explanation entry if they are the same idea).
"""

    def _build_source_instruction(text):

        if not text:
            return ""

        return (
            "\nSOURCE MATERIAL (study this deeply and design the "
            "lesson from your own understanding of it - do not copy "
            "or lightly trim its sentences into note_text or "
            "spoken_teacher_script):\n"
            f"{text}\n"
            "END SOURCE MATERIAL.\n"
        )

    def _build_user_prompt(source_instruction):

        return f"""
Create a complete classroom lesson following the required teaching
model exactly.

TOPIC: {topic}
GRADE: {grade}
SUBJECT: {subject}
CHAPTER: {chapter}
LANGUAGE: {language}
PREVIOUS LESSON TOPIC: {previous_topic}

{source_instruction}

Follow the greeting -> teacher introduction -> lesson introduction ->
notebook instruction -> note-taking -> note completion -> detailed
explanation -> application -> summary -> tiered assessment flow
exactly, with separate structured fields as instructed.
Return ONLY valid JSON.
"""

    # ========================================================
    # TPM-AWARE SIZING
    #
    # 1. Measure the prompt WITHOUT source material first, to
    #    know the fixed cost that is always present.
    # 2. If the full source_text still leaves room for at least
    #    MIN_COMPLETION_TOKENS under AI_TPM_LIMIT, use it as-is.
    # 3. Otherwise, truncate source_text down to whatever fits,
    #    with a clear note appended so the AI (and the returned
    #    lesson) isn't silently working from a half-cut sentence
    #    - this keeps a long textbook excerpt from causing a
    #    hard failure when a shorter (but still real) lesson
    #    could have been generated instead.
    # 4. If even a fully-empty source_text doesn't leave room for
    #    MIN_COMPLETION_TOKENS (the fixed system prompt itself is
    #    too large for this account's TPM limit), fail with a
    #    clear, actionable message instead of sending a request
    #    that is guaranteed to be rejected.
    # ========================================================

    base_prompt_tokens = _estimate_tokens(system_prompt) + _estimate_tokens(
        _build_user_prompt(_build_source_instruction(""))
    )

    if base_prompt_tokens + MIN_COMPLETION_TOKENS + RATE_LIMIT_SAFETY_MARGIN > AI_TPM_LIMIT:

        raise RuntimeError(
            "This account's Groq tokens-per-minute limit "
            f"({AI_TPM_LIMIT}) is too low to generate a lesson at "
            "all, even with no source material. Upgrade the Groq "
            "account tier, or raise AI_TPM_LIMIT if the account "
            "actually allows more."
        )

    effective_source_text = source_text

    if source_text:

        source_budget_tokens = (
            AI_TPM_LIMIT
            - base_prompt_tokens
            - MIN_COMPLETION_TOKENS
            - RATE_LIMIT_SAFETY_MARGIN
        )

        source_tokens = _estimate_tokens(source_text)

        if source_tokens > source_budget_tokens:

            source_char_budget = max(
                0,
                source_budget_tokens * CHARS_PER_TOKEN_ESTIMATE
            )

            effective_source_text = (
                source_text[:source_char_budget].rstrip()
                + "\n[SOURCE MATERIAL TRUNCATED TO FIT THE "
                "AVAILABLE TOKEN BUDGET - the portion above is "
                "still real source text to study and build the "
                "lesson from.]"
            )

    source_instruction = _build_source_instruction(effective_source_text)

    user_prompt = _build_user_prompt(source_instruction)

    prompt_tokens = _estimate_tokens(system_prompt) + _estimate_tokens(user_prompt)

    available_completion_tokens = (
        AI_TPM_LIMIT
        - prompt_tokens
        - RATE_LIMIT_SAFETY_MARGIN
    )

    if available_completion_tokens < MIN_COMPLETION_TOKENS:
        raise RuntimeError(
            "The lesson request is too large for the configured Groq "
            f"TPM limit of {AI_TPM_LIMIT} tokens. Shorten the "
            "'Additional Information' source text or add more API "
            "keys to GROQ_API_KEYS."
        )

    request_max_tokens = min(
        AI_MAX_TOKENS,
        available_completion_tokens,
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    content = _call_ai_with_rotation(
        messages=messages,
        temperature=0.25,
        json_mode=True,
        max_tokens=request_max_tokens,
    )

    content = _clean_json_response(content)

    try:
        lesson = json.loads(content)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"AI returned invalid JSON: {error}")

    if not isinstance(lesson, dict):
        raise RuntimeError("AI returned JSON, but the result is not a lesson object.")

    # ========================================================
    # DEFAULTS / NORMALIZATION
    #
    # Every field the rest of the pipeline (lesson_parser.py,
    # app.py) reads is guaranteed to exist and be the right
    # type here, so downstream code never has to guess.
    # ========================================================

    lesson["title"] = _as_text(lesson.get("title")) or topic

    lesson["subject"] = _as_text(lesson.get("subject")) or subject

    lesson["chapter"] = _as_text(lesson.get("chapter")) or chapter

    lesson["grade"] = _as_text(lesson.get("grade")) or grade

    lesson["language"] = _as_text(lesson.get("language")) or language

    lesson["previous_lesson_review"] = _as_text(
        lesson.get("previous_lesson_review")
    )

    lesson["greeting"] = _as_text(lesson.get("greeting"))

    lesson["teacher_introduction"] = _as_text(
        lesson.get("teacher_introduction")
    )

    lesson["lesson_introduction"] = _as_text(
        lesson.get("lesson_introduction")
    )

    lesson["objectives"] = _normalize_objectives(
        lesson.get("objectives")
    )

    lesson["notebook_instruction"] = _as_text(
        lesson.get("notebook_instruction")
    )

    lesson["note_sections"] = _normalize_note_sections(
        lesson.get("note_sections")
    )

    lesson["note_completion"] = _as_text(
        lesson.get("note_completion")
    )

    lesson["explanation_sections"] = _normalize_explanation_sections(
        lesson.get("explanation_sections")
    )

    lesson["application_activity"] = _as_text(
        lesson.get("application_activity")
    )

    lesson["summary"] = _as_text(lesson.get("summary"))

    lesson["assessment"] = _normalize_assessment(
        lesson.get("assessment")
    )

    lesson["closing_phrase"] = REQUIRED_CLOSING_PHRASE

    return lesson


# ============================================================
# STUDENT QUESTION EXPLANATION
# ============================================================

def generate_explanation(
    topic,
    question,
    language="English",
):

    prompt = f"""
You are Teacher Birhan, a classroom teacher.
Topic: {topic}
Question: {question}
Language: {language}

Answer only the student's question clearly, simply, and with one example.
"""

    return _call_ai_with_rotation(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )