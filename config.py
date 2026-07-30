import os
from pathlib import Path
from dotenv import load_dotenv

# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================

load_dotenv()

# ============================================================
# BASE DIRECTORY
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

# ============================================================
# DATA ROOT
# ============================================================

DATA_ROOT = Path(os.getenv("DATA_ROOT", str(BASE_DIR)))

# ============================================================
# AI API CONFIGURATION
# ============================================================
#
# IMPORTANT:
# Put ALL Groq API keys in ONE variable:
#
# GROQ_API_KEYS=key1,key2,key3,key4,...,key15,...
#
# There is NO fixed limit in this code.
# Add as many comma-separated keys as your environment supports.
#
# The old single-key GROQ_API_KEY is also supported as a fallback.
# ============================================================

_raw_api_keys = os.getenv("GROQ_API_KEYS", "").strip()

AI_API_KEYS = []

if _raw_api_keys:
    for key in _raw_api_keys.split(","):
        key = key.strip()
        if key:
            AI_API_KEYS.append(key)

# Backward compatibility with the old single-key variable.
if not AI_API_KEYS:
    single_key = os.getenv("GROQ_API_KEY", "").strip()

    if single_key:
        AI_API_KEYS.append(single_key)

# Remove duplicate keys while preserving their order.
AI_API_KEYS = list(dict.fromkeys(AI_API_KEYS))

AI_API_KEY_COUNT = len(AI_API_KEYS)

AI_BASE_URL = os.getenv(
    "AI_BASE_URL",
    "https://api.groq.com/openai/v1",
).strip()

AI_MODEL = os.getenv(
    "AI_MODEL",
    "llama-3.3-70b-versatile",
).strip()

AI_MAX_TOKENS = int(
    os.getenv("AI_MAX_TOKENS", "8000")
)

AI_TPM_LIMIT = int(
    os.getenv("AI_TPM_LIMIT", "12000")
)

# ============================================================
# REAL REFERENCE IMAGE LOOKUP
# ============================================================

PEXELS_API_KEY = os.getenv(
    "PEXELS_API_KEY",
    "",
).strip()

# ============================================================
# FLASK
# ============================================================

SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "birhan-ai-development-secret-key",
)

# ============================================================
# DIRECTORIES
# ============================================================

STATIC_DIR = DATA_ROOT / "static"
AUDIO_DIR = STATIC_DIR / "audio"
VIDEO_DIR = STATIC_DIR / "videos"
IMAGE_DIR = STATIC_DIR / "images"
GENERATED_IMAGE_DIR = IMAGE_DIR / "generated"
DIAGRAM_DIR = IMAGE_DIR / "diagrams"
DATA_DIR = DATA_ROOT / "data"
DATABASE_PATH = DATA_DIR / "database.db"
FONTS_DIR = BASE_DIR / "fonts"

REQUIRED_DIRECTORIES = [
    STATIC_DIR,
    AUDIO_DIR,
    VIDEO_DIR,
    IMAGE_DIR,
    GENERATED_IMAGE_DIR,
    DIAGRAM_DIR,
    DATA_DIR,
    FONTS_DIR,
]

for directory in REQUIRED_DIRECTORIES:
    directory.mkdir(parents=True, exist_ok=True)

# ============================================================
# VIDEO CONFIGURATION
# ============================================================

VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 24

# ============================================================
# UPLOAD LIMIT
# ============================================================

MAX_UPLOAD_SIZE_MB = 100
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024

# ============================================================
# PAUSE / TIMING
# ============================================================

APPLAUSE_PAUSE_SECONDS = 4
THINKING_PAUSE_SECONDS = 4
CLOSING_PAUSE_SECONDS = 2

# ============================================================
# RENDER WORKERS
# ============================================================

_cpu_count = os.cpu_count() or 2

RENDER_WORKERS = max(
    1,
    min(
        int(
            os.getenv(
                "RENDER_WORKERS",
                str(max(1, _cpu_count - 1)),
            )
        ),
        _cpu_count,
    ),
)

RENDER_SCENE_THREADS = max(
    1,
    int(os.getenv("RENDER_SCENE_THREADS", "1")),
)

FFMPEG_FALLBACK_THREADS = max(
    1,
    int(
        os.getenv(
            "FFMPEG_FALLBACK_THREADS",
            str(max(1, _cpu_count - 1)),
        )
    ),
)
