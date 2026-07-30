from pathlib import Path


def ensure_directory(path):
    path = Path(path)
    path.mkdir(
        parents=True,
        exist_ok=True
    )
    return path


def safe_filename(filename):
    return "".join(
        character
        for character in filename
        if character.isalnum()
        or character in (
            "_",
            "-",
            "."
        )
    )