# © Copyright 2026 Aaron Kimball
"""Sample `Model` implementations and JSON fixtures used to test `ModelRegistry`, `Session`,
`SystemPrompt`, and `Role` against a stable, non-production set of models.
"""

from pathlib import Path

from fixtures.sample_models.alpha_model import AlphaModel
from fixtures.sample_models.beta_model import BetaModel
from fixtures.sample_models.gamma_model import GammaModel

from klorb.models.registry import ModelRegistry

NO_SUCH_DIR = Path(__file__).parent / "_no_such_dir"
"""A directory that's guaranteed never to exist, passed as `ModelRegistry`'s
`packaged_models_dir`/`user_models_dir` when a test wants a registry that scans nothing."""


def sample_model_registry() -> ModelRegistry:
    """Return a `ModelRegistry` scanning no directories but directly
    `register()`-ing `AlphaModel`, `BetaModel`, and `GammaModel`.
    """
    registry = ModelRegistry(packaged_models_dir=NO_SUCH_DIR, user_models_dir=NO_SUCH_DIR)
    for model in (AlphaModel(), BetaModel(), GammaModel()):
        registry.register(model)
    return registry
