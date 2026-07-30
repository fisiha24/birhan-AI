"""
Nigat / Birhan AI
Visual Detector

Detects educational visual type from
short classroom board content.

IMPORTANT:
This detector NEVER uses the full teacher narration
as board content.

The following fields are NOT used as board text:
- spoken_teacher_script
- narration
- introduction
- greeting
- summary
- encouragement
- explanation
"""

import re


# ============================================================
# CLEAN TEXT
# ============================================================

def clean_text(value):

    if value is None:
        return ""

    if isinstance(value, list):

        return " ".join(
            clean_text(item)
            for item in value
        )

    if isinstance(value, dict):

        return " ".join(
            clean_text(item)
            for item in value.values()
        )

    return str(value).strip()


# ============================================================
# NORMALIZE TEXT
# ============================================================

def normalize_text(value):

    value = clean_text(value)

    value = value.lower()

    value = value.replace(
        "-",
        " "
    )

    value = re.sub(
        r"[^a-z0-9\s]",
        " ",
        value
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


# ============================================================
# WORD BOUNDARY MATCH
# ============================================================

def contains_term(text, term):

    text = normalize_text(text)

    term = normalize_text(term)

    if not text or not term:

        return False

    pattern = (
        r"\b"
        + re.escape(term)
        + r"\b"
    )

    return bool(
        re.search(
            pattern,
            text
        )
    )


# ============================================================
# COMBINE SCENE TEXT
# ============================================================

def get_scene_text(scene):

    if not isinstance(
        scene,
        dict
    ):

        return ""

    """
    IMPORTANT FIX

    Only short educational content should be used
    for visual detection.

    NEVER use:
    - spoken_teacher_script
    - narration
    - introduction
    - greeting
    - summary
    - encouragement

    This prevents the teacher's full speech from
    being treated as visual or board content.
    """

    values = [

        scene.get(
            "title",
            ""
        ),

        scene.get(
            "board_text",
            ""
        ),

        scene.get(
            "key_points",
            []
        ),

        scene.get(
            "example",
            ""
        ),

        scene.get(
            "check_question",
            ""
        ),

        scene.get(
            "visual_suggestion",
            ""
        ),

        scene.get(
            "visual_query",
            ""
        )

    ]

    combined_text = " ".join(

        clean_text(value)

        for value in values

        if clean_text(value)

    )

    return normalize_text(
        combined_text
    )


# ============================================================
# VISUAL DETECTION RULES
# ============================================================

VISUAL_RULES = {

    "water-cycle": {

        "strong": [
            "water cycle",
            "hydrologic cycle",
            "rain cycle"
        ],

        "medium": [
            "evaporation",
            "condensation",
            "precipitation",
            "water vapor"
        ],

        "weak": [
            "rainfall",
            "collection"
        ]

    },

    "food-chain": {

        "strong": [
            "food chain",
            "food web",
            "energy flow"
        ],

        "medium": [
            "producer",
            "consumer",
            "decomposer"
        ],

        "weak": [
            "herbivore",
            "carnivore",
            "omnivore",
            "predator",
            "prey"
        ]

    },

    "states-of-matter": {

        "strong": [
            "states of matter",
            "state of matter",
            "change of state"
        ],

        "medium": [
            "melting",
            "freezing",
            "boiling",
            "sublimation"
        ],

        "weak": [
            "solid",
            "liquid",
            "gas"
        ]

    },

    "electricity": {

        "strong": [
            "electric circuit",
            "electrical circuit",
            "electric current",
            "electricity",
            "electric charge"
        ],

        "medium": [
            "voltage",
            "battery",
            "circuit",
            "conductor",
            "insulator"
        ],

        "weak": [
            "switch",
            "bulb"
        ]

    },

    "solar-system": {

        "strong": [
            "solar system",
            "solar system model",
            "planetary system"
        ],

        "medium": [
            "planet",
            "planets",
            "orbit",
            "moon"
        ],

        "weak": [
            "mercury",
            "venus",
            "earth",
            "mars",
            "jupiter",
            "saturn",
            "uranus",
            "neptune"
        ]

    },

    "human-body": {

        "strong": [
            "human body",
            "body system",
            "digestive system",
            "respiratory system",
            "circulatory system",
            "nervous system",
            "skeletal system",
            "muscular system"
        ],

        "medium": [
            "heart",
            "lung",
            "lungs",
            "brain",
            "stomach",
            "intestine",
            "blood",
            "bone",
            "muscle"
        ],

        "weak": []

    },

    "simple-machine": {

        "strong": [
            "simple machine",
            "mechanical advantage"
        ],

        "medium": [
            "wheel and axle",
            "inclined plane"
        ],

        "weak": [
            "lever",
            "pulley",
            "wedge",
            "screw"
        ]

    },

    "flowering-plant": {

        "strong": [
            "flowering plant",
            "flowering plants",
            "flowering"
        ],

        "medium": [
            "flower",
            "flower parts",
            "reproduction in flowering plants"
        ],

        "weak": [
            "petal",
            "sepal",
            "stamen",
            "pistil",
            "ovary"
        ]

    },

    "non-flowering-plant": {

        "strong": [
            "non flowering plant",
            "non flowering plants",
            "nonflowering plant",
            "nonflowering plants"
        ],

        "medium": [
            "fern",
            "moss",
            "cone bearing plant"
        ],

        "weak": [
            "spore"
        ]

    },

    "plant": {

        "strong": [
            "plant kingdom",
            "plants",
            "plant"
        ],

        "medium": [
            "root",
            "stem",
            "leaf",
            "leaves",
            "seed",
            "germination",
            "photosynthesis",
            "chlorophyll"
        ],

        "weak": [
            "plant cell",
            "plant tissue"
        ]

    },

    "animal": {

        "strong": [
            "animal kingdom",
            "classification of animals",
            "animals",
            "animal"
        ],

        "medium": [
            "mammal",
            "mammals",
            "bird",
            "birds",
            "fish",
            "amphibian",
            "amphibians",
            "reptile",
            "reptiles"
        ],

        "weak": [
            "wild animal",
            "domestic animal"
        ]

    },

    "vertebrate": {

        "strong": [
            "vertebrate animals",
            "vertebrates",
            "vertebrate",
            "animals with backbone",
            "animals with a backbone"
        ],

        "medium": [
            "mammal",
            "mammals",
            "bird",
            "birds",
            "fish",
            "amphibian",
            "amphibians",
            "reptile",
            "reptiles"
        ],

        "weak": [
            "backbone",
            "spinal column"
        ]

    },

    "invertebrate": {

        "strong": [
            "invertebrate animals",
            "invertebrates",
            "invertebrate",
            "animals without backbone",
            "animals without a backbone"
        ],

        "medium": [
            "insect",
            "insects",
            "worm",
            "worms",
            "mollusk",
            "mollusks",
            "mollusc",
            "molluscs",
            "arachnid",
            "arachnids"
        ],

        "weak": [
            "no backbone",
            "without backbone"
        ]

    },

    "cell": {

        "strong": [
            "cell structure",
            "cell biology",
            "cell theory",
            "cells",
            "cell"
        ],

        "medium": [
            "plant cell",
            "animal cell",
            "cell membrane",
            "cell wall",
            "cytoplasm",
            "nucleus",
            "chloroplast",
            "mitochondria"
        ],

        "weak": [
            "organelle",
            "organelles"
        ]

    },

    "measurement": {

        "strong": [
            "scientific measurement",
            "physical measurement",
            "measurement"
        ],

        "medium": [
            "measuring",
            "length",
            "mass",
            "volume",
            "time"
        ],

        "weak": [
            "hand span",
            "handspan",
            "digit",
            "cubit",
            "pace",
            "foot",
            "arm span",
            "fathom",
            "shadow",
            "sundial"
        ]

    },

    "force-motion": {

        "strong": [
            "force and motion",
            "force and movement",
            "motion and force"
        ],

        "medium": [
            "force",
            "motion",
            "movement"
        ],

        "weak": [
            "push",
            "pull",
            "speed"
        ]

    }

}


# ============================================================
# SCORE WEIGHTS
# ============================================================

STRONG_SCORE = 10

MEDIUM_SCORE = 4

WEAK_SCORE = 1


# ============================================================
# CALCULATE VISUAL SCORE
# ============================================================

def calculate_visual_score(
    text,
    rules
):

    score = 0

    matched_terms = []

    for term in rules.get(
        "strong",
        []
    ):

        if contains_term(
            text,
            term
        ):

            score += STRONG_SCORE

            matched_terms.append(
                term
            )

    for term in rules.get(
        "medium",
        []
    ):

        if contains_term(
            text,
            term
        ):

            score += MEDIUM_SCORE

            matched_terms.append(
                term
            )

    for term in rules.get(
        "weak",
        []
    ):

        if contains_term(
            text,
            term
        ):

            score += WEAK_SCORE

            matched_terms.append(
                term
            )

    return (
        score,
        matched_terms
    )


# ============================================================
# DETECT VISUAL TYPE
# ============================================================

def detect_visual_type(scene):

    text = get_scene_text(
        scene
    )

    if not text:

        return "educational"

    scores = {}

    matched_keywords = {}

    for visual_type, rules in VISUAL_RULES.items():

        score, matched_terms = calculate_visual_score(
            text,
            rules
        )

        scores[visual_type] = score

        matched_keywords[visual_type] = matched_terms

    ranked_visuals = sorted(
        scores.items(),
        key=lambda item: item[1],
        reverse=True
    )

    if not ranked_visuals:

        return "educational"

    best_visual_type, best_score = ranked_visuals[0]

    if best_score < 4:

        return "educational"

    best_matches = matched_keywords.get(
        best_visual_type,
        []
    )

    weak_only_terms = [

        "earth",
        "sun",
        "moon",
        "planet",
        "current",
        "foot",
        "time",
        "mass",
        "volume",
        "length",
        "solid",
        "liquid",
        "gas"

    ]

    if len(best_matches) == 1:

        if best_matches[0] in weak_only_terms:

            return "educational"

    return best_visual_type


# ============================================================
# APPLY DETECTOR TO SCENE
# ============================================================

def add_visual_type(scene):

    if not isinstance(
        scene,
        dict
    ):

        return scene

    detected_scene = dict(
        scene
    )

    detected_scene["visual_type"] = detect_visual_type(
        detected_scene
    )

    return detected_scene


# ============================================================
# APPLY DETECTOR TO ALL SCENES
# ============================================================

def detect_scene_visuals(scenes):

    if not isinstance(
        scenes,
        list
    ):

        return []

    detected_scenes = []

    for scene in scenes:

        if not isinstance(
            scene,
            dict
        ):

            continue

        detected_scenes.append(
            add_visual_type(
                scene
            )
        )

    return detected_scenes