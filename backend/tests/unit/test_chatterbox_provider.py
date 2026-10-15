from __future__ import annotations

import sys
from types import ModuleType

import pytest

from app.config import Settings
from app.core.errors import TerminalError
from app.providers.tts.chatterbox import (
    ChatterboxOriginalTTS,
    ChatterboxTurboTTS,
    _is_transient_model_load_error,
    _require_real_perth_watermarker,
)
from app.providers.tts.resolver import build_provider, voice_for


def test_original_chatterbox_is_available_as_the_local_production_provider() -> None:
    configured = Settings(  # type: ignore[call-arg]
        TTS_PROVIDER="chatterbox",
        TTS_FALLBACK_PROVIDER="none",
    )

    assert isinstance(build_provider("chatterbox"), ChatterboxOriginalTTS)
    assert isinstance(build_provider("chatterbox-turbo"), ChatterboxTurboTTS)
    assert voice_for("chatterbox").voice == "builtin-default"
    assert configured.TTS_PROVIDER == "chatterbox"
    assert configured.TTS_FALLBACK_PROVIDER == "none"


def test_reviewed_original_chatterbox_controls_are_persisted() -> None:
    configured = Settings()  # type: ignore[call-arg]

    assert configured.CHATTERBOX_MODEL == "ResembleAI/chatterbox"
    assert configured.CHATTERBOX_EXAGGERATION == 0.5
    assert configured.CHATTERBOX_CFG_WEIGHT == 0.4
    assert configured.CHATTERBOX_TEMPERATURE == 0.8
    assert configured.CHATTERBOX_REPETITION_PENALTY == 1.2
    assert configured.CHATTERBOX_MIN_P == 0.05
    assert configured.CHATTERBOX_TOP_P == 1.0


def test_non_callable_perth_watermarker_exposes_hidden_import_error(monkeypatch) -> None:
    perth = ModuleType("perth")
    perth.__path__ = []  # type: ignore[attr-defined]
    perth.PerthImplicitWatermarker = None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "perth", perth)

    with pytest.raises(TerminalError, match="PerthImplicitWatermarker is unavailable") as exc:
        _require_real_perth_watermarker()

    assert isinstance(exc.value.__cause__, ModuleNotFoundError)


def test_callable_real_perth_watermarker_is_accepted(monkeypatch) -> None:
    perth = ModuleType("perth")
    perth.PerthImplicitWatermarker = type("RealWatermarker", (), {})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "perth", perth)

    _require_real_perth_watermarker()


def test_model_load_error_classification_is_conservative() -> None:
    assert _is_transient_model_load_error(TimeoutError("download timed out"))
    assert _is_transient_model_load_error(ConnectionError("connection reset"))
    assert not _is_transient_model_load_error(TypeError("dependency is not callable"))
    assert not _is_transient_model_load_error(RuntimeError("unsupported runtime"))


@pytest.mark.media
def test_worker_has_real_perth_watermarker() -> None:
    perth = pytest.importorskip("perth")

    assert callable(perth.PerthImplicitWatermarker)
    assert perth.PerthImplicitWatermarker.__name__ == "PerthImplicitWatermarker"
