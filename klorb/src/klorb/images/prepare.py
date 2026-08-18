# © Copyright 2026 Aaron Kimball
"""Resize/downsample/transcode pipeline for a raw image file headed into a vision model's
prompt content.
"""

import base64
import io
import logging
from typing import Any

from PIL import Image, ImageOps
from pydantic import BaseModel

from klorb.models.model import Model

logger = logging.getLogger(__name__)

_PIL_FORMAT_BY_MIME_TYPE: dict[str, str] = {
    "image/webp": "WEBP",
    "image/png": "PNG",
    "image/jpeg": "JPEG"
}
"""Every output format `prepare_image_for_model` is willing to transcode to. Restricted to
lossless WebP/PNG plus a JPEG fallback."""

_EXTENSION_BY_MIME_TYPE: dict[str, str] = {
    "image/webp": "webp",
    "image/png": "png",
    "image/jpeg": "jpg",
}

_MAX_DOWNSCALE_RETRIES = 3
"""How many times `prepare_image_for_model` halves the target pixel budget and re-encodes
before giving up on `ImagePipelineConfig.max_bytes_raw`, e.g. a screenshot that's still huge
even after the model's own resolution cap is applied."""

_RETRY_SCALE_FACTOR = 0.75


class ImageTooLargeError(Exception):
    """Raised by `prepare_image_for_model` when an image still exceeds `ImagePipelineConfig.
    max_bytes_raw` after `_MAX_DOWNSCALE_RETRIES` rounds of further downscaling."""


class ImagePipelineConfig(BaseModel):
    """The `tools.images.*` settings `prepare_image_for_model` needs, extracted from
    `klorb.process_config.ProcessConfig` by the caller rather than imported here directly."""

    default_max_dimension_px: int
    max_bytes_raw: int
    preferred_formats: list[str]


class PreparedImage(BaseModel):
    """The result of `prepare_image_for_model`: everything a caller needs to build an
    `image_url`-type `klorb.message.MessageFragment`."""

    mime_type: str
    data_b64: str
    width: int
    height: int
    original_width: int
    original_height: int
    byte_size: int
    """Size of the encoded (post-transcode, pre-base64) image bytes."""


def _target_box(
    original_width: int, original_height: int, model: Model, config: ImagePipelineConfig,
) -> tuple[int, int]:
    """Compute the downscale-only target `(width, height)` for `model`'s `vision_details`
    (whichever of `max_width_px`/`max_height_px`/`max_megapixels` it declares), or `config.
    default_max_dimension_px` as a long-edge cap for a model with no `vision_details`. Never
    upscales: an image already within bounds keeps its original size."""
    vision_details: dict[str, Any] = model.capabilities().get("vision_details") or {}
    width, height = original_width, original_height

    max_width = vision_details.get("max_width_px")
    max_height = vision_details.get("max_height_px")
    if max_width or max_height:
        scale = min(
            (max_width / width) if max_width else 1.0,
            (max_height / height) if max_height else 1.0,
            1.0,
        )
        width, height = round(width * scale), round(height * scale)

    max_megapixels = vision_details.get("max_megapixels")
    if max_megapixels:
        max_pixels = max_megapixels * 1_000_000
        if width * height > max_pixels:
            scale = (max_pixels / (width * height)) ** 0.5
            width, height = round(width * scale), round(height * scale)

    if not vision_details:
        long_edge = config.default_max_dimension_px
        scale = min(long_edge / max(width, height, 1), 1.0)
        width, height = round(width * scale), round(height * scale)

    return max(width, 1), max(height, 1)


def _choose_mime_type(model: Model, config: ImagePipelineConfig) -> str:
    """Pick the first of `config.preferred_formats` that `model`'s `vision_details.
    supported_mime_types` actually lists (or the first preferred format outright, for a model
    that declares no `vision_details`/`supported_mime_types` at all), falling through to
    `"image/jpeg"` only if none of the preferred (lossless) formats are supported."""
    vision_details: dict[str, Any] = model.capabilities().get("vision_details") or {}
    supported = vision_details.get("supported_mime_types")
    for mime_type in config.preferred_formats:
        if supported is None or mime_type in supported:
            return mime_type
    return "image/jpeg"


def _encode(image: Image.Image, mime_type: str) -> bytes:
    """Encode `image` as `mime_type` into memory. WebP/PNG are saved lossless; JPEG uses
    quality 90. `image.save()` is never passed `exif=`, so whatever EXIF block
    `ImageOps.exif_transpose()` left on `image.info` never reaches the output bytes."""
    buffer = io.BytesIO()
    pil_format = _PIL_FORMAT_BY_MIME_TYPE[mime_type]
    if pil_format == "WEBP":
        image.save(buffer, format=pil_format, lossless=True)
    elif pil_format == "JPEG":
        image.save(buffer, format=pil_format, quality=90)
    else:
        image.save(buffer, format=pil_format)
    return buffer.getvalue()


def prepare_image_for_model(raw_bytes: bytes, model: Model, config: ImagePipelineConfig) -> PreparedImage:
    """Resize, transcode, and base64-encode `raw_bytes` for `model`'s vision input.

    EXIF orientation is baked in and then dropped entirely from the output. The image is
    downscaled (never upscaled) to fit `model`'s `vision_details` bounds, or
    `config.default_max_dimension_px` for a model with no declared bounds, and transcoded to
    the first of `config.preferred_formats` the model supports.

    If the encoded result still exceeds `config.max_bytes_raw`, the target box is
    shrunk by `_RETRY_SCALE_FACTOR` and re-encoded, up to `_MAX_DOWNSCALE_RETRIES` times,
    before raising `ImageTooLargeError`.
    """
    image: Image.Image = Image.open(io.BytesIO(raw_bytes))
    image = ImageOps.exif_transpose(image) or image
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.mode else "RGB")
    original_width, original_height = image.width, image.height

    mime_type = _choose_mime_type(model, config)
    target_width, target_height = _target_box(original_width, original_height, model, config)

    encoded = b""
    width, height = target_width, target_height
    for attempt in range(_MAX_DOWNSCALE_RETRIES + 1):
        resized = image
        if (width, height) != (original_width, original_height):
            resized = image.resize((width, height), Image.Resampling.BICUBIC)
        encoded = _encode(resized, mime_type)
        if len(encoded) <= config.max_bytes_raw:
            width, height = resized.width, resized.height
            break
        logger.debug(
            "Encoded image (%d bytes) exceeds image_max_bytes_raw=%d on attempt %d; "
            "downscaling further.", len(encoded), config.max_bytes_raw, attempt)
        width = max(round(width * _RETRY_SCALE_FACTOR), 1)
        height = max(round(height * _RETRY_SCALE_FACTOR), 1)
    else:
        raise ImageTooLargeError(
            f"image too large for {model.name()}; try a smaller image or a different model")

    return PreparedImage(
        mime_type=mime_type,
        data_b64=base64.b64encode(encoded).decode("ascii"),
        width=width,
        height=height,
        original_width=original_width,
        original_height=original_height,
        byte_size=len(encoded),
    )


def extension_for_mime_type(mime_type: str) -> str:
    """The file extension `klorb.workspace.session_store.write_session_image` uses when
    spilling a prepared image's bytes to disk, for `mime_type`. Falls back to `"bin"` for
    an unrecognized mime type rather than raising, since a wrong extension is cosmetic while
    raising here would turn an already-transcoded, already-validated image into a
    lost attachment.
    """
    return _EXTENSION_BY_MIME_TYPE.get(mime_type, "bin")
