"""
Nigat / Birhan AI
Educational Lesson Video Generator

SHARED-RENDERER / RENDER-STABILITY VERSION
"""

import logging
import math
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from moviepy.editor import (
    VideoClip,
    AudioClip,
    AudioFileClip,
    concatenate_audioclips,
)

from config import (
    VIDEO_WIDTH,
    VIDEO_HEIGHT,
    RENDER_WORKERS,
    RENDER_SCENE_THREADS,
    FFMPEG_FALLBACK_THREADS,
)

from services.board_renderer import (
    assign_visual_style,
    build_scene_render_layout,
    create_scene_frame_at_time,
)


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(__name__)


# ============================================================
# VIDEO / ENCODING SETTINGS
# ============================================================

FPS = 15

VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"
VIDEO_BITRATE = "2500k"
VIDEO_PRESET = "ultrafast"

CONCATENATION_METHOD = "chain"


# ============================================================
# CREATE ONE SCENE'S VIDEO + AUDIO CLIP
# ============================================================

def create_scene_clip(image_file, audio_file, scene):

    image_file = Path(image_file)
    audio_file = Path(audio_file)

    if not image_file.exists():
        raise FileNotFoundError(
            f"Image file not found: {image_file}"
        )

    if not audio_file.exists():
        raise FileNotFoundError(
            f"Audio file not found: {audio_file}"
        )

    try:
        with Image.open(image_file) as test_image:
            test_image.verify()
    except Exception as error:
        raise ValueError(
            f"Invalid image file: {image_file}"
        ) from error

    audio_clip = AudioFileClip(str(audio_file))

    duration = float(audio_clip.duration)

    if not duration or duration <= 0:
        audio_clip.close()
        raise ValueError(
            f"Invalid audio duration: {audio_file}"
        )

    # --------------------------------------------------------
    # AUDIO / VIDEO SYNC
    # --------------------------------------------------------

    frame_aligned_duration = (
        math.ceil(duration * FPS) / FPS
    )

    pad_seconds = (
        frame_aligned_duration - duration
    )

    if pad_seconds > 0.0005:

        silence_clip = AudioClip(
            lambda t: 0,
            duration=pad_seconds,
            fps=44100,
        )

        audio_clip = concatenate_audioclips(
            [
                audio_clip,
                silence_clip,
            ]
        )

    duration = frame_aligned_duration

    # --------------------------------------------------------
    # BUILD BOARD LAYOUT ONCE
    # --------------------------------------------------------
    #
    # IMPORTANT:
    # We intentionally avoid passing a color argument to
    # Image.new() because some Pylance/Pillow type definitions
    # incorrectly interpret that argument as an integer.
    #
    # The image is created first, then filled with white using
    # ImageDraw.rectangle().
    # --------------------------------------------------------

    layout_image = Image.new(
        "RGB",
        (
            VIDEO_WIDTH,
            VIDEO_HEIGHT,
        ),
    )

    layout_draw = ImageDraw.Draw(layout_image)

    layout_draw.rectangle(
        [
            0,
            0,
            VIDEO_WIDTH - 1,
            VIDEO_HEIGHT - 1,
        ],
        fill=(255, 255, 255),
    )

    scene_layout = build_scene_render_layout(
        draw=layout_draw,
        scene=scene,
        duration=duration,
    )

    # --------------------------------------------------------
    # FRAME GENERATOR
    # --------------------------------------------------------

    def make_frame(time_position):

        safe_time = max(
            0.0,
            min(
                float(time_position),
                duration,
            ),
        )

        pil_image = create_scene_frame_at_time(
            scene=scene,
            time_position=safe_time,
            duration=duration,
            layout=scene_layout,
        )

        frame_array = np.asarray(
            pil_image.convert("RGB"),
            dtype=np.uint8,
        )

        if (
            frame_array.ndim != 3
            or frame_array.shape[2] != 3
        ):
            raise ValueError(
                "Generated frame must be an RGB NumPy array."
            )

        if (
            frame_array.shape[0] != VIDEO_HEIGHT
            or frame_array.shape[1] != VIDEO_WIDTH
        ):
            raise ValueError(
                "Generated frame has the wrong dimensions."
            )

        return frame_array

    video_clip = VideoClip(
        make_frame=make_frame,
        duration=duration,
    )

    video_clip = video_clip.set_audio(
        audio_clip
    )

    return video_clip, audio_clip


# ============================================================
# RENDER ONE SCENE TO ITS OWN MP4
# ============================================================

def _render_single_scene_to_file(
    scene_index,
    image_file,
    audio_file,
    scene,
    temp_dir,
    threads,
):

    scene_clip, audio_clip = create_scene_clip(
        image_file=image_file,
        audio_file=audio_file,
        scene=scene,
    )

    scene_output_path = (
        Path(temp_dir)
        / f"scene_{scene_index:05d}.mp4"
    )

    try:

        scene_clip.write_videofile(
            str(scene_output_path),
            fps=FPS,
            codec=VIDEO_CODEC,
            audio_codec=AUDIO_CODEC,
            preset=VIDEO_PRESET,
            bitrate=VIDEO_BITRATE,
            threads=max(
                1,
                int(threads),
            ),
            temp_audiofile=str(
                Path(temp_dir)
                / f"scene_{scene_index:05d}_temp_audio.m4a"
            ),
            remove_temp=True,
            logger=None,
        )

    finally:

        try:
            scene_clip.close()
        except Exception:
            pass

        try:
            audio_clip.close()
        except Exception:
            pass

    return (
        scene_index,
        str(scene_output_path),
    )


# ============================================================
# PROCESS POOL WRAPPER
# ============================================================

def _render_single_scene_job(args):

    (
        scene_index,
        image_file,
        audio_file,
        scene,
        temp_dir,
        threads,
    ) = args

    return _render_single_scene_to_file(
        scene_index=scene_index,
        image_file=image_file,
        audio_file=audio_file,
        scene=scene,
        temp_dir=temp_dir,
        threads=threads,
    )


# ============================================================
# FAST FFMPEG CONCATENATION
# ============================================================

def _concat_scene_files_fast(
    scene_files,
    output_file,
    temp_dir,
):

    concat_list_path = (
        Path(temp_dir)
        / "concat_list.txt"
    )

    with open(
        concat_list_path,
        "w",
        encoding="utf-8",
    ) as handle:

        for scene_file in scene_files:

            escaped = str(
                scene_file
            ).replace(
                "'",
                "'\\''",
            )

            handle.write(
                f"file '{escaped}'\n"
            )

    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_path),
        "-c",
        "copy",
        str(output_file),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:

        raise RuntimeError(
            "Fast ffmpeg concat failed:\n"
            + result.stderr
        )


# ============================================================
# PARALLEL VIDEO CREATION
# ============================================================

def _create_video_parallel(
    image_files,
    audio_files,
    output_file,
    scenes,
):

    with tempfile.TemporaryDirectory(
        prefix="birhan_render_"
    ) as temp_dir:

        jobs = [
            (
                index,
                str(image_file),
                str(audio_file),
                scene,
                temp_dir,
                RENDER_SCENE_THREADS,
            )
            for index, (
                image_file,
                audio_file,
                scene,
            ) in enumerate(
                zip(
                    image_files,
                    audio_files,
                    scenes,
                )
            )
        ]

        rendered = {}

        with ProcessPoolExecutor(
            max_workers=RENDER_WORKERS
        ) as executor:

            futures = {
                executor.submit(
                    _render_single_scene_job,
                    job,
                ): job[0]
                for job in jobs
            }

            completed = 0

            for future in as_completed(
                futures
            ):

                scene_index, scene_output_path = (
                    future.result()
                )

                rendered[
                    scene_index
                ] = scene_output_path

                completed += 1

                logger.info(
                    "Rendered scene %s/%s (parallel)",
                    completed,
                    len(jobs),
                )

        ordered_files = [
            rendered[index]
            for index in sorted(rendered)
        ]

        logger.info(
            "Joining %s rendered scene files: %s",
            len(ordered_files),
            output_file,
        )

        _concat_scene_files_fast(
            ordered_files,
            output_file,
            temp_dir,
        )


# ============================================================
# SEQUENTIAL FALLBACK
# ============================================================

def _create_video_sequential(
    image_files,
    audio_files,
    output_file,
    scenes,
):

    with tempfile.TemporaryDirectory(
        prefix="birhan_render_seq_"
    ) as temp_dir:

        rendered_files = []

        total = len(image_files)

        for index, (
            image_file,
            audio_file,
            scene,
        ) in enumerate(
            zip(
                image_files,
                audio_files,
                scenes,
            )
        ):

            logger.info(
                "Rendering scene %s/%s "
                "(sequential fallback)",
                index + 1,
                total,
            )

            _, scene_output_path = (
                _render_single_scene_to_file(
                    scene_index=index,
                    image_file=str(
                        image_file
                    ),
                    audio_file=str(
                        audio_file
                    ),
                    scene=scene,
                    temp_dir=temp_dir,
                    threads=FFMPEG_FALLBACK_THREADS,
                )
            )

            rendered_files.append(
                scene_output_path
            )

        logger.info(
            "Joining %s rendered scene files: %s",
            len(rendered_files),
            output_file,
        )

        _concat_scene_files_fast(
            rendered_files,
            output_file,
            temp_dir,
        )


# ============================================================
# CREATE COMPLETE VIDEO
# ============================================================

def create_video(
    image_files,
    audio_files,
    output_file,
    scenes=None,
):

    logger.info(
        "Starting classroom board video generation"
    )

    output_file = Path(
        output_file
    )

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # VALIDATION
    # --------------------------------------------------------

    if not image_files:
        raise ValueError(
            "No image files were provided."
        )

    if not audio_files:
        raise ValueError(
            "No audio files were provided."
        )

    if len(image_files) != len(
        audio_files
    ):
        raise ValueError(
            "The number of images and audio files "
            "must match."
        )

    if scenes is None:
        scenes = [
            {}
            for _ in image_files
        ]

    if len(scenes) != len(
        image_files
    ):
        raise ValueError(
            "The number of scenes must match "
            "the number of images."
        )

    # --------------------------------------------------------
    # SAFETY NET:
    # Assign board themes and camera angles if app.py
    # has not already done so.
    # --------------------------------------------------------

    if (
        scenes
        and isinstance(
            scenes[0],
            dict,
        )
        and "board_theme" not in scenes[0]
    ):

        assign_visual_style(
            scenes
        )

    # --------------------------------------------------------
    # SELECT RENDER METHOD
    # --------------------------------------------------------

    use_parallel = (
        len(image_files) > 1
        and RENDER_WORKERS > 1
    )

    # --------------------------------------------------------
    # PARALLEL RENDER
    # --------------------------------------------------------

    if use_parallel:

        try:

            _create_video_parallel(
                image_files=image_files,
                audio_files=audio_files,
                output_file=output_file,
                scenes=scenes,
            )

            logger.info(
                "Classroom board video created "
                "successfully (parallel)."
            )

            return output_file

        except Exception:

            logger.exception(
                "Parallel rendering failed - "
                "falling back to the sequential renderer."
            )

    # --------------------------------------------------------
    # SEQUENTIAL FALLBACK
    # --------------------------------------------------------

    _create_video_sequential(
        image_files=image_files,
        audio_files=audio_files,
        output_file=output_file,
        scenes=scenes,
    )

    logger.info(
        "Classroom board video created "
        "successfully (sequential)."
    )

    return output_file