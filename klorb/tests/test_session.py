# © Copyright 2026 Aaron Kimball
"""Tests for klorb.session."""

from unittest.mock import MagicMock

import fixtures.sample_models as sample_models_package

from klorb.models.registry import ModelRegistry
from klorb.session import Session
from klorb.session import SessionConfig


def test_session_config_defaults() -> None:
    config = SessionConfig()

    assert config.interactive is True
    assert config.log_filename is None


def test_session_saves_config() -> None:
    config = SessionConfig(model="some/model", interactive=False, log_filename="a.log")
    session = Session(config, provider=MagicMock())

    assert session.config is config


def test_active_model_name_falls_back_to_config_model_when_unregistered() -> None:
    config = SessionConfig(model="some/unregistered-model")
    session = Session(config, provider=MagicMock())

    assert session.active_model_name() == "some/unregistered-model"


def test_active_model_name_invokes_registered_model_name() -> None:
    config = SessionConfig(model="alpha")
    registry = ModelRegistry(package=sample_models_package)
    session = Session(config, provider=MagicMock(), model_registry=registry)

    assert session.active_model_name() == "alpha"


def test_send_turn_sends_prompt_to_active_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = "model reply"
    config = SessionConfig(model="some/model")
    session = Session(config, provider=mock_provider)

    response = session.send_turn("hi")

    assert response == "model reply"
    mock_provider.send_prompt.assert_called_once_with("hi", model="some/model")


def test_run_one_shot_delegates_to_send_turn() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = "model reply"
    config = SessionConfig(model="some/model")
    session = Session(config, provider=mock_provider)

    response = session.run_one_shot("hi")

    assert response == "model reply"
    mock_provider.send_prompt.assert_called_once_with("hi", model="some/model")
