# © Copyright 2026 Aaron Kimball
"""Tests for klorb.server.klorb_agent._extract_prompt_content -- splitting ACP prompt content
blocks into plain text plus image MessageFragments, and its vision-capability/audio-block
rejection rules."""

import base64
import io

import acp
import pytest
from PIL import Image

from klorb.models.configured_model import ConfiguredModel
from klorb.process_config import ProcessConfig
from klorb.server.klorb_agent import _extract_prompt_content


def _image_data_b64(width: int = 100, height: int = 50) -> str:
    image = Image.new("RGB", (width, height), (10, 20, 30))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _vision_model() -> ConfiguredModel:
    return ConfiguredModel({"name": "test/vision", "capabilities": {"vision": True}}, source="test")


def _non_vision_model() -> ConfiguredModel:
    return ConfiguredModel({"name": "test/no-vision", "capabilities": {"vision": False}}, source="test")


class TestExtractPromptContent:
    def test_concatenates_text_blocks(self) -> None:
        text, images = _extract_prompt_content(
            [acp.text_block("hello "), acp.text_block("world")], _vision_model(), ProcessConfig())
        assert text == "hello world"
        assert images == []

    def test_builds_an_image_fragment_for_a_vision_model(self) -> None:
        block = acp.image_block(data=_image_data_b64(), mime_type="image/png")

        text, images = _extract_prompt_content([block], _vision_model(), ProcessConfig())

        assert text == ""
        assert len(images) == 1
        fragment = images[0]
        assert fragment.type == "image_url"
        assert fragment.image_url is not None
        assert fragment.mime_type is not None
        assert fragment.original_width == 100
        assert fragment.original_height == 50

    def test_rejects_image_block_when_active_model_has_no_vision(self) -> None:
        block = acp.image_block(data=_image_data_b64(), mime_type="image/png")
        with pytest.raises(acp.RequestError):
            _extract_prompt_content([block], _non_vision_model(), ProcessConfig())

    def test_rejects_image_block_when_there_is_no_active_model(self) -> None:
        block = acp.image_block(data=_image_data_b64(), mime_type="image/png")
        with pytest.raises(acp.RequestError):
            _extract_prompt_content([block], None, ProcessConfig())

    def test_still_rejects_audio_blocks(self) -> None:
        block = acp.audio_block(data="xx", mime_type="audio/wav")
        with pytest.raises(acp.RequestError):
            _extract_prompt_content([block], _vision_model(), ProcessConfig())

    def test_reads_filename_from_block_meta(self) -> None:
        block = acp.image_block(data=_image_data_b64(), mime_type="image/png")
        block = block.model_copy(update={"field_meta": {"klorb": {"filename": "shot.png"}}})

        _, images = _extract_prompt_content([block], _vision_model(), ProcessConfig())

        assert images[0].source_filename == "shot.png"

    def test_no_filename_meta_means_source_filename_is_none(self) -> None:
        block = acp.image_block(data=_image_data_b64(), mime_type="image/png")

        _, images = _extract_prompt_content([block], _vision_model(), ProcessConfig())

        assert images[0].source_filename is None

    def test_wraps_image_too_large_error_as_invalid_params(self) -> None:
        block = acp.image_block(data=_image_data_b64(400, 400), mime_type="image/png")
        config = ProcessConfig(image_max_bytes_raw=1)
        with pytest.raises(acp.RequestError):
            _extract_prompt_content([block], _vision_model(), config)
