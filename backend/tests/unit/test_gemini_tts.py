"""Gemini TTS provider, retry/fallback policy, and secret hygiene.

Every Gemini call here is mocked — the regular suite must never need a live API
key or spend quota. The one live check is a manual smoke test, documented in
the README.

The most important tests in this file are the ones asserting what must *not*
happen: no fallback on configuration errors, and no API key in any string the
system can emit.
"""

from __future__ import annotations

import base64
import struct
import wave
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.core.errors import RetryableError, TerminalError
from app.providers.base import AudioResult, VoiceSpec
from app.providers.tts.direction import (
    SYSTEMDECODED_DIRECTION,
    build_prompt,
)
from app.providers.tts.gemini import (
    PREBUILT_VOICES,
    GeminiTTS,
    _rate_from_mime,
    peak_amplitude,
    write_pcm_as_wav,
)
from app.providers.tts.resolver import ResilientTTS

FAKE_KEY = "AIza-TOTALLY-FAKE-KEY-FOR-TESTS-0000"


def pcm(seconds: float = 1.0, rate: int = 24_000, amplitude: int = 8000) -> bytes:
    """Deterministic 16-bit mono PCM, like Gemini's audio/l16 payload."""
    n = int(seconds * rate)
    return struct.pack(f"<{n}h", *([amplitude, -amplitude] * (n // 2) + [0] * (n % 2)))


def gemini_ok(audio: bytes, rate: int = 24_000) -> dict:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": f"audio/l16; rate={rate}; channels=1",
                                "data": base64.b64encode(audio).decode(),
                            }
                        }
                    ]
                }
            }
        ],
        "modelVersion": "gemini-3.1-flash-tts-preview",
        "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 300},
    }


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    from app.config import settings

    monkeypatch.setattr(settings, "GEMINI_API_KEY", SecretStr(FAKE_KEY))
    monkeypatch.setattr(settings, "GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
    monkeypatch.setattr(settings, "GEMINI_TTS_VOICE", "Charon")
    monkeypatch.setattr(settings, "TTS_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "TTS_FALLBACK_PROVIDER", "kokoro")
    monkeypatch.setattr(settings, "GEMINI_MAX_ATTEMPTS", 3)


def mock_transport(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """Route the provider's httpx calls to `handler`, recording requests."""
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(recording)
    real = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real(*args, **kwargs)

    from app.providers.tts import gemini as gemini_module

    monkeypatch.setattr(gemini_module.httpx, "AsyncClient", patched)
    return seen


# ------------------------------------------------------------- WAV wrapping ---
def test_raw_pcm_is_wrapped_in_a_readable_wav(tmp_path: Path) -> None:
    """Gemini returns headerless audio/l16; a bare .wav write is unopenable."""
    out = tmp_path / "a.wav"
    duration = write_pcm_as_wav(pcm(2.0), out, sample_rate=24_000)

    assert duration == pytest.approx(2.0, abs=0.01)
    with wave.open(str(out), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 24_000
        assert w.getnframes() == 48_000


def test_odd_byte_count_is_trimmed_not_corrupted(tmp_path: Path) -> None:
    out = tmp_path / "odd.wav"
    write_pcm_as_wav(pcm(0.5) + b"\x01", out)
    with wave.open(str(out), "rb") as w:
        assert w.getnframes() == 12_000


@pytest.mark.parametrize(
    ("mime", "expected"),
    [
        ("audio/l16; rate=24000; channels=1", 24_000),
        ("audio/l16;rate=16000", 16_000),
        ("audio/l16", 24_000),
        ("", 24_000),
        ("audio/l16; rate=notanumber", 24_000),
    ],
)
def test_sample_rate_is_parsed_from_mime(mime: str, expected: int) -> None:
    """A wrong rate would pitch-shift narration and desync every caption."""
    assert _rate_from_mime(mime) == expected


# ------------------------------------------------------------- the request ---
async def test_request_carries_model_voice_and_audio_modality(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    seen = mock_transport(
        monkeypatch, lambda r: httpx.Response(200, json=gemini_ok(pcm(1.0)))
    )
    await GeminiTTS().synthesize("Nobody sent it.", VoiceSpec(voice="Kore"), tmp_path / "o.wav")

    request = seen[0]
    assert "gemini-3.1-flash-tts-preview:generateContent" in str(request.url)
    body = json.loads(request.content)
    assert body["generationConfig"]["responseModalities"] == ["AUDIO"]
    voice_cfg = body["generationConfig"]["speechConfig"]["voiceConfig"]
    assert voice_cfg["prebuiltVoiceConfig"]["voiceName"] == "Kore"


async def test_request_includes_the_style_direction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sending the bare transcript is what produces generic AI cadence."""
    import json

    seen = mock_transport(
        monkeypatch, lambda r: httpx.Response(200, json=gemini_ok(pcm(1.0)))
    )
    await GeminiTTS().synthesize("Nobody sent it.", VoiceSpec(voice="Charon"), tmp_path / "o.wav")

    text = json.loads(seen[0].content)["contents"][0]["parts"][0]["text"]
    assert "technology storyteller" in text
    assert "Avoid" in text
    assert text.rstrip().endswith("Nobody sent it.")


def test_direction_is_centralised_not_duplicated() -> None:
    assert build_prompt("hi", None) == "hi"
    assert "storyteller" in build_prompt("hi", SYSTEMDECODED_DIRECTION)


# ------------------------------------------------------------ the response ---
async def test_successful_synthesis_reports_provider_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mock_transport(monkeypatch, lambda r: httpx.Response(200, json=gemini_ok(pcm(2.5))))
    result = await GeminiTTS().synthesize("x", VoiceSpec(voice="Charon"), tmp_path / "o.wav")

    assert result.provider == "gemini"
    assert result.voice == "Charon"
    assert result.model == "gemini-3.1-flash-tts-preview"
    assert result.sample_rate == 24_000
    assert result.fallback_used is False
    assert result.duration_seconds == pytest.approx(2.5, abs=0.01)
    assert result.path.exists()


async def test_asset_metadata_is_serialisable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import json

    mock_transport(monkeypatch, lambda r: httpx.Response(200, json=gemini_ok(pcm(1.0))))
    result = await GeminiTTS().synthesize("x", VoiceSpec(voice="Charon"), tmp_path / "o.wav")
    meta = result.as_asset_metadata()

    json.dumps(meta)  # must survive JSONB storage
    assert meta["provider"] == "gemini"
    assert meta["fallback_used"] is False
    assert meta["generated_at"]


async def test_response_without_audio_is_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Our parsing bug, not the provider's outage — must not trigger fallback."""
    mock_transport(monkeypatch, lambda r: httpx.Response(200, json={"candidates": []}))
    with pytest.raises(TerminalError, match="no audio"):
        await GeminiTTS().synthesize("x", VoiceSpec(voice="Charon"), tmp_path / "o.wav")


async def test_empty_audio_payload_is_retryable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mock_transport(monkeypatch, lambda r: httpx.Response(200, json=gemini_ok(b"")))
    with pytest.raises(RetryableError):
        await GeminiTTS().synthesize("x", VoiceSpec(voice="Charon"), tmp_path / "o.wav")


# --------------------------------------------------- error classification ---
@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
async def test_transient_statuses_are_retryable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int
) -> None:
    mock_transport(
        monkeypatch,
        lambda r: httpx.Response(status, json={"error": {"message": "busy"}}),
    )
    with pytest.raises(RetryableError):
        await GeminiTTS().synthesize("x", VoiceSpec(voice="Charon"), tmp_path / "o.wav")


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_client_errors_are_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int
) -> None:
    """Bad credentials or a bad request are ours to fix — never fall back."""
    mock_transport(
        monkeypatch,
        lambda r: httpx.Response(status, json={"error": {"message": "nope"}}),
    )
    with pytest.raises(TerminalError):
        await GeminiTTS().synthesize("x", VoiceSpec(voice="Charon"), tmp_path / "o.wav")


async def test_network_failure_is_retryable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    mock_transport(monkeypatch, boom)
    with pytest.raises(RetryableError):
        await GeminiTTS().synthesize("x", VoiceSpec(voice="Charon"), tmp_path / "o.wav")


async def test_missing_api_key_is_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pydantic import SecretStr

    from app.config import settings

    monkeypatch.setattr(settings, "GEMINI_API_KEY", SecretStr(""))
    with pytest.raises(TerminalError, match="GEMINI_API_KEY"):
        await GeminiTTS().synthesize("x", VoiceSpec(voice="Charon"), tmp_path / "o.wav")


async def test_unknown_voice_is_terminal_before_any_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = mock_transport(monkeypatch, lambda r: httpx.Response(200, json=gemini_ok(pcm())))
    with pytest.raises(TerminalError, match="Unknown Gemini voice"):
        await GeminiTTS().synthesize("x", VoiceSpec(voice="Gandalf"), tmp_path / "o.wav")
    assert seen == [], "a bad voice must not cost an API call"


async def test_empty_text_is_terminal(tmp_path: Path) -> None:
    with pytest.raises(TerminalError, match="empty"):
        await GeminiTTS().synthesize("   ", VoiceSpec(voice="Charon"), tmp_path / "o.wav")


# ------------------------------------------------------------ secret safety ---
def test_api_key_never_appears_in_settings_repr() -> None:
    settings = Settings(GEMINI_API_KEY=FAKE_KEY)
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings.GEMINI_API_KEY)
    assert settings.GEMINI_API_KEY.get_secret_value() == FAKE_KEY


async def test_api_key_never_appears_in_error_messages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An error that echoed the request headers would leak the key into logs."""
    mock_transport(
        monkeypatch,
        lambda r: httpx.Response(403, json={"error": {"message": "API key invalid"}}),
    )
    with pytest.raises(TerminalError) as excinfo:
        await GeminiTTS().synthesize("x", VoiceSpec(voice="Charon"), tmp_path / "o.wav")
    assert FAKE_KEY not in str(excinfo.value)
    assert FAKE_KEY not in repr(excinfo.value)


async def test_api_key_is_sent_as_a_header_not_a_query_param(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Query strings land in access logs and proxy logs; headers do not."""
    seen = mock_transport(
        monkeypatch, lambda r: httpx.Response(200, json=gemini_ok(pcm(1.0)))
    )
    await GeminiTTS().synthesize("x", VoiceSpec(voice="Charon"), tmp_path / "o.wav")

    assert FAKE_KEY not in str(seen[0].url)
    assert seen[0].headers["x-goog-api-key"] == FAKE_KEY


def test_config_flags_gemini_without_a_key() -> None:
    problems = Settings(TTS_PROVIDER="gemini", GEMINI_API_KEY="").validate_runtime()
    assert any("GEMINI_API_KEY" in p for p in problems)
    assert any("NOT covered by the Kokoro fallback" in p for p in problems)


# ------------------------------------------------------- retry and fallback ---
class StubTTS:
    """A provider that fails a set number of times, then succeeds."""

    def __init__(self, name: str, fail_times: int = 0, error: Exception | None = None):
        self.name = name
        self.fail_times = fail_times
        self.error = error or RetryableError("transient")
        self.calls = 0

    async def list_voices(self) -> list[str]:
        return ["stub"]

    async def synthesize(self, text: str, voice: VoiceSpec, out_path: Path) -> AudioResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error
        out_path.parent.mkdir(parents=True, exist_ok=True)
        write_pcm_as_wav(pcm(1.0), out_path)
        return AudioResult(
            path=out_path, duration_seconds=1.0, sample_rate=24_000,
            provider=self.name, voice=voice.voice, model=f"{self.name}-model",
        )


async def test_retries_then_succeeds_without_falling_back(tmp_path: Path) -> None:
    primary = StubTTS("gemini", fail_times=2)
    fallback = StubTTS("kokoro")
    result = await ResilientTTS(primary, fallback, max_attempts=3, backoff=_noop).synthesize(
        "x", VoiceSpec(voice="Charon"), tmp_path / "o.wav"
    )

    assert primary.calls == 3
    assert fallback.calls == 0
    assert result.provider == "gemini"
    assert result.fallback_used is False


async def test_falls_back_after_retries_are_exhausted(tmp_path: Path) -> None:
    primary = StubTTS("gemini", fail_times=99)
    fallback = StubTTS("kokoro")
    result = await ResilientTTS(primary, fallback, max_attempts=3, backoff=_noop).synthesize(
        "x", VoiceSpec(voice="Charon"), tmp_path / "o.wav"
    )

    assert primary.calls == 3
    assert fallback.calls == 1
    assert result.provider == "kokoro"
    assert result.fallback_used is True
    assert result.metadata["fallback_from"] == "gemini"
    assert result.metadata["primary_attempts"] == 3


async def test_fallback_result_never_claims_to_be_the_primary(tmp_path: Path) -> None:
    """Recording the wrong provider would make the audio impossible to explain."""
    result = await ResilientTTS(
        StubTTS("gemini", fail_times=99), StubTTS("kokoro"), max_attempts=2, backoff=_noop
    ).synthesize("x", VoiceSpec(voice="Charon"), tmp_path / "o.wav")

    assert result.provider != "gemini"
    assert result.as_asset_metadata()["fallback_used"] is True


async def test_configuration_errors_never_fall_back(tmp_path: Path) -> None:
    """The rule this whole design exists to enforce."""
    primary = StubTTS("gemini", fail_times=99, error=TerminalError("GEMINI_API_KEY is not set"))
    fallback = StubTTS("kokoro")

    with pytest.raises(TerminalError, match="GEMINI_API_KEY"):
        await ResilientTTS(primary, fallback, max_attempts=3, backoff=_noop).synthesize(
            "x", VoiceSpec(voice="Charon"), tmp_path / "o.wav"
        )

    assert primary.calls == 1, "a terminal error must not be retried"
    assert fallback.calls == 0, "a config error must never be masked by fallback"


async def test_no_fallback_configured_raises_after_exhaustion(tmp_path: Path) -> None:
    primary = StubTTS("gemini", fail_times=99)
    with pytest.raises(RetryableError, match="no fallback"):
        await ResilientTTS(primary, None, max_attempts=2, backoff=_noop).synthesize(
            "x", VoiceSpec(voice="Charon"), tmp_path / "o.wav"
        )


def test_voice_namespaces_do_not_leak_between_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kokoro's am_puck means nothing to Gemini, and vice versa."""
    from app.config import settings
    from app.providers.tts.resolver import voice_for

    monkeypatch.setattr(settings, "GEMINI_TTS_VOICE", "Charon")
    monkeypatch.setattr(settings, "TTS_VOICE", "am_puck")
    assert voice_for("gemini").voice == "Charon"
    assert voice_for("kokoro").voice == "am_puck"


def test_unknown_provider_name_is_terminal() -> None:
    from app.providers.tts.resolver import build_provider

    with pytest.raises(TerminalError, match="Unknown TTS provider"):
        build_provider("elevenlabs")


# ------------------------------------------------------------- auditions ---
async def test_audition_generates_one_file_per_voice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.services.voice_audition import generate_auditions

    mock_transport(monkeypatch, lambda r: httpx.Response(200, json=gemini_ok(pcm(3.0))))
    monkeypatch.setattr("app.services.voice_audition.asyncio.sleep", _noop)

    auditions = await generate_auditions(voices=("Charon", "Kore"), out_dir=tmp_path)

    assert [a.voice for a in auditions] == ["Charon", "Kore"]
    assert (tmp_path / "gemini_charon.wav").exists()
    assert (tmp_path / "gemini_kore.wav").exists()


async def test_audition_uses_identical_script_for_every_voice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Different prompts per voice would make the comparison meaningless."""
    import json

    from app.services.voice_audition import generate_auditions

    seen = mock_transport(
        monkeypatch, lambda r: httpx.Response(200, json=gemini_ok(pcm(3.0)))
    )
    monkeypatch.setattr("app.services.voice_audition.asyncio.sleep", _noop)
    await generate_auditions(voices=("Charon", "Kore", "Puck"), out_dir=tmp_path)

    prompts = {json.loads(r.content)["contents"][0]["parts"][0]["text"] for r in seen}
    assert len(prompts) == 1, "all voices must read the same prompt"


async def test_audition_normalises_peak_across_voices(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Otherwise the loudest voice always sounds like the best voice."""
    from app.services.voice_audition import TARGET_PEAK, generate_auditions

    quiet = pcm(2.0, amplitude=1200)
    mock_transport(monkeypatch, lambda r: httpx.Response(200, json=gemini_ok(quiet)))
    monkeypatch.setattr("app.services.voice_audition.asyncio.sleep", _noop)

    audition = (await generate_auditions(voices=("Charon",), out_dir=tmp_path))[0]

    assert audition.peak_before < 0.1
    assert audition.peak_after == pytest.approx(TARGET_PEAK, abs=0.02)


async def test_audition_rejects_unknown_voices_without_substituting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A silently swapped voice would label audio with a name it isn't."""
    from app.services.voice_audition import generate_auditions

    seen = mock_transport(monkeypatch, lambda r: httpx.Response(200, json=gemini_ok(pcm())))
    with pytest.raises(TerminalError, match="Unknown Gemini voice"):
        await generate_auditions(voices=("Charon", "Nonexistent"), out_dir=tmp_path)
    assert seen == []


def test_requested_shortlist_is_valid_for_this_model() -> None:
    from app.services.voice_audition import DEFAULT_SHORTLIST

    assert set(DEFAULT_SHORTLIST) <= set(PREBUILT_VOICES)


def test_peak_amplitude_bounds() -> None:
    assert peak_amplitude(b"") == 0.0
    assert peak_amplitude(pcm(0.1, amplitude=32767)) == pytest.approx(1.0, abs=0.01)


async def _noop(*args, **kwargs) -> None:
    return None
