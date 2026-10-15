from __future__ import annotations

import pytest

from app.core.errors import TerminalError
from app.providers.alignment.faster_whisper import FasterWhisperAligner


class _IncompatibleModel:
    def transcribe(self, *args, **kwargs):
        raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")


async def test_deterministic_alignment_runtime_error_is_terminal(
    monkeypatch, tmp_path
) -> None:
    aligner = FasterWhisperAligner()
    aligner._model = _IncompatibleModel()

    with pytest.raises(TerminalError, match="Alignment runtime is incompatible"):
        await aligner.align(tmp_path / "audio.wav", "approved transcript")


@pytest.mark.media
def test_worker_pyav_is_compatible_with_released_faster_whisper() -> None:
    av = pytest.importorskip("av")

    assert int(av.__version__.split(".")[0]) < 19
