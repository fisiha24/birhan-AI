"""
Birhan AI
Static Scene Visual Generator (Gallery Images)

============================================================
WHY THIS FILE EXISTS / WHAT WAS FIXED
============================================================

app.py imports create_scene_visual() from services.visual_generator,
but this file either did not exist or was out of sync with the
actual video rendering code - which is why the "generated image"
shown for a scene did not reliably match what actually appeared in
the rendered video (different fonts, colors, themes, or missing
diagram panels).

This file now renders the gallery PNG using the EXACT SAME shared
renderer (services/board_renderer.py) that services/video_generator.py
uses to draw every video frame. Both call
board_renderer.create_scene_frame_at_time() with the scene's own
board_theme/camera_angle already assigned (see
board_renderer.assign_visual_style(), called once in app.py on the
complete scene list), so the still image and the video frame for a
given scene are now guaranteed to be pixel-logic-identical (same
theme, same camera framing, same diagram icon, same fully-written
board text).
"""

from pathlib import Path

from PIL import Image

from services.board_renderer import create_scene_frame_at_time


def create_scene_visual(scene, output_path):

    """
    Renders the FINAL state of a scene's board (i.e. every word of
    that scene's board text fully written) as a static PNG, using
    the identical drawing code path used for the video's own last
    frame of this scene.
    """

    output_path = Path(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # time_position == duration guarantees every board word that
    # would ever appear for this scene is shown, regardless of
    # whether real speech timing has been attached to the scene yet
    # (it may not be - app.py generates the image before the audio).
    image = create_scene_frame_at_time(
        scene=scene,
        time_position=1.0,
        duration=1.0,
    )

    if image.mode != "RGB":
        image = image.convert("RGB")

    image.save(str(output_path), format="PNG")

    return image