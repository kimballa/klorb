# © Copyright 2026 Aaron Kimball
"""Tests for klorb.images.prepare -- prepare_image_for_model's resize/format/byte-ceiling
pipeline."""

import io

import pytest
from PIL import Image

from klorb.images.prepare import ImagePipelineConfig, ImageTooLargeError, prepare_image_for_model
from klorb.models.configured_model import ConfiguredModel


def _config(
    default_max_dimension_px: int = 1568,
    max_bytes_raw: int = 26214400,
    preferred_formats: list[str] | None = None,
) -> ImagePipelineConfig:
    return ImagePipelineConfig(
        default_max_dimension_px=default_max_dimension_px,
        max_bytes_raw=max_bytes_raw,
        preferred_formats=preferred_formats or ["image/webp", "image/png"],
    )


def _model(vision_details: dict[str, object] | None = None) -> ConfiguredModel:
    capabilities: dict[str, object] = {"vision": True}
    if vision_details is not None:
        capabilities["vision_details"] = vision_details
    return ConfiguredModel({"name": "test/vision-model", "capabilities": capabilities}, source="test")


def _png_bytes(width: int, height: int, color: tuple[int, int, int] = (200, 50, 50)) -> bytes:
    image = Image.new("RGB", (width, height), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class TestTargetBox:
    def test_never_upscales_a_small_image(self) -> None:
        prepared = prepare_image_for_model(_png_bytes(100, 50), _model(), _config())
        assert (prepared.width, prepared.height) == (100, 50)

    def test_downscales_to_default_max_dimension_when_model_has_no_vision_details(self) -> None:
        prepared = prepare_image_for_model(
            _png_bytes(3000, 1000), _model(vision_details=None), _config(default_max_dimension_px=300))
        assert prepared.width == 300
        assert prepared.height == 100

    def test_downscales_to_max_width_height_box_preserving_aspect_ratio(self) -> None:
        prepared = prepare_image_for_model(
            _png_bytes(1000, 500), _model({"max_width_px": 100, "max_height_px": 100}), _config())
        assert prepared.width == 100
        assert prepared.height == 50

    def test_downscales_to_max_megapixels(self) -> None:
        prepared = prepare_image_for_model(
            _png_bytes(2000, 2000), _model({"max_megapixels": 1.0}), _config())
        assert prepared.width * prepared.height <= 1_000_000
        assert prepared.width == prepared.height  # square image stays square


class TestFormatSelection:
    def test_prefers_first_preferred_format_the_model_supports(self) -> None:
        prepared = prepare_image_for_model(
            _png_bytes(10, 10), _model({"supported_mime_types": ["image/png"]}),
            _config(preferred_formats=["image/webp", "image/png"]))
        assert prepared.mime_type == "image/png"

    def test_falls_back_to_jpeg_when_no_preferred_format_is_supported(self) -> None:
        prepared = prepare_image_for_model(
            _png_bytes(10, 10), _model({"supported_mime_types": ["image/jpeg"]}),
            _config(preferred_formats=["image/webp", "image/png"]))
        assert prepared.mime_type == "image/jpeg"

    def test_uses_first_preferred_format_when_model_declares_no_supported_list(self) -> None:
        prepared = prepare_image_for_model(
            _png_bytes(10, 10), _model(vision_details=None),
            _config(preferred_formats=["image/png", "image/webp"]))
        assert prepared.mime_type == "image/png"


class TestByteCeiling:
    def test_raises_image_too_large_error_when_ceiling_cannot_be_met(self) -> None:
        with pytest.raises(ImageTooLargeError):
            prepare_image_for_model(_png_bytes(400, 400), _model(), _config(max_bytes_raw=1))


class TestExifOrientation:
    def test_bakes_in_exif_rotation_and_strips_exif_from_output(self) -> None:
        image = Image.new("RGB", (200, 100), (10, 20, 30))
        exif = image.getexif()
        exif[274] = 6  # Orientation: rotate 270 CW (i.e. rotate 90 CCW) to display upright.
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", exif=exif.tobytes())

        prepared = prepare_image_for_model(buffer.getvalue(), _model(), _config())

        # exif_transpose() applies the orientation tag, swapping width/height for tag 6.
        assert (prepared.original_width, prepared.original_height) == (100, 200)
