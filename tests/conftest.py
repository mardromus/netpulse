"""Shared, session-scoped fixtures: the generated week is analysed once."""

import pytest

from netpulse.generate import GenConfig, generate
from netpulse.pipeline import analyze


@pytest.fixture(scope="session")
def generated():
    return generate(GenConfig())


@pytest.fixture(scope="session")
def week_result(generated):
    return analyze(generated.samples, {e.name: e for e in generated.entities}, generated.truth)
