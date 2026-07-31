"""
Birhan AI
Static Scene Visual Generator

Uses the same board renderer as the video generator.
"""

from pathlib import Path

from services.board_renderer import (
    create_scene_frame_at_time,
)


def create_scene_visual(
    scene,
    output_path,
):
    """
    Create the final still image for a scene.

    Uses the same renderer used by the video generator.
    """

    output_path = Path(
        output_path
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    image = create_scene_frame_at_time(
        scene=scene,
        time_position=1.0,
        duration=1.0,
    )

    if image.mode != "RGB":

        image = image.convert(
            "RGB"
        )

    image.save(
        str(output_path),
        format="PNG",
    )

    return image
