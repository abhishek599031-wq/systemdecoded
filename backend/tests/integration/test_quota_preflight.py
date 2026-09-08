"""A render must not start unless the provider can finish it.

The failure being prevented, precisely: a five-block render asked Gemini for
narration, the free tier's daily allowance ran out after block one, and the rest
came from the fallback. Every component behaved correctly and the video was
still not the one that had been asked for.

The tests here assert the guarantee from both ends — that an insufficient quota
produces *zero* synthesis requests of any kind, and that a sufficient one is
still allowed through.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import QuotaBlockedError, QuotaExhaustedError, RetryableError
from app.models.enums import ProjectStatus
from app.models.quota import ProviderQuotaUsage
from app.services import production, quota
from app.services.quota import QuotaState
from app.services.seed_first_video import seed_first_video

MODEL = "gemini-3.1-flash-tts-preview"


async def _spend(session, used: int, *, exhausted: bool = False, limit: int | None = None):
    """Put the ledger in a known state for today."""
    session.add(
        ProviderQuotaUsage(
            provider="gemini",
            model=MODEL,
            usage_date=quota.quota_day(),
            requests_used=used,
            observed_limit=limit,
            exhausted_at=datetime.now(UTC) if exhausted else None,
        )
    )
    await session.flush()


async def _project_ready_to_render(session):
    project = await seed_first_video(session)
    from tests.integration.test_production_pipeline import revise_first_video_for

    await revise_first_video_for(session, project)
    for target in [
        ProjectStatus.IDEA_APPROVED, ProjectStatus.RESEARCHING, ProjectStatus.RESEARCH_READY,
        ProjectStatus.SCRIPT_GENERATING, ProjectStatus.SCRIPT_REVIEW,
        ProjectStatus.SCRIPT_APPROVED, ProjectStatus.PRODUCTION_PLANNING,
        ProjectStatus.ASSETS_READY,
    ]:
        from app.core.state_machine import transition

        await transition(session, project, target, reason="test")
    await session.commit()
    return project


class _RecordingTTS:
    """Fails the test if it is ever asked to speak."""

    prefers_whole_block = True

    def __init__(self, name: str = "gemini") -> None:
        self.name = name
        self.primary_name = name
        self.calls: list[str] = []

    async def list_voices(self):
        return ["Algieba"]

    async def synthesize(self, text, voice, out_path):
        self.calls.append(text)
        raise AssertionError("synthesize() must not be reached when quota is insufficient")


# ------------------------------------------------------- the decision itself ---
async def test_sufficient_quota_allows_the_render(session, monkeypatch) -> None:
    """TEST 1 — required 5, available 5."""
    monkeypatch.setattr(quota.settings, "GEMINI_TTS_DAILY_REQUEST_LIMIT", 10)
    await _spend(session, used=5)

    decision = await quota.preflight(session, "gemini", MODEL, required_requests=5)

    assert decision.state is QuotaState.SUFFICIENT
    assert decision.allowed is True
    assert decision.available_requests == 5


async def test_partial_quota_blocks_the_render(session, monkeypatch) -> None:
    """TEST 2 — required 5, available 3. Enough for most of a video is not enough."""
    monkeypatch.setattr(quota.settings, "GEMINI_TTS_DAILY_REQUEST_LIMIT", 10)
    await _spend(session, used=7)

    decision = await quota.preflight(session, "gemini", MODEL, required_requests=5)

    assert decision.state is QuotaState.INSUFFICIENT
    assert decision.allowed is False
    assert decision.available_requests == 3
    assert "required 5" in decision.reason and "available 3" in decision.reason


async def test_no_quota_blocks_the_render(session, monkeypatch) -> None:
    """TEST 3 — required 5, available 0."""
    monkeypatch.setattr(quota.settings, "GEMINI_TTS_DAILY_REQUEST_LIMIT", 10)
    await _spend(session, used=10)

    decision = await quota.preflight(session, "gemini", MODEL, required_requests=5)

    assert decision.state is QuotaState.INSUFFICIENT
    assert decision.available_requests == 0


async def test_exactly_enough_is_enough(session, monkeypatch) -> None:
    """The boundary is not off by one in the direction that wastes a render."""
    monkeypatch.setattr(quota.settings, "GEMINI_TTS_DAILY_REQUEST_LIMIT", 10)
    await _spend(session, used=5)
    assert (await quota.preflight(session, "gemini", MODEL, 5)).allowed is True

    await session.rollback()
    await _spend(session, used=6)
    assert (await quota.preflight(session, "gemini", MODEL, 5)).allowed is False


async def test_unknown_quota_blocks(session, monkeypatch) -> None:
    """TEST 4 — an unknown allowance is not evidence of an available one."""
    monkeypatch.setattr(quota.settings, "GEMINI_TTS_DAILY_REQUEST_LIMIT", -1)

    decision = await quota.preflight(session, "gemini", MODEL, required_requests=5)

    assert decision.state is QuotaState.UNKNOWN
    assert decision.allowed is False


async def test_billing_enabled_removes_the_cap(session, monkeypatch) -> None:
    """0 means no daily cap, which is different from an unknown one."""
    monkeypatch.setattr(quota.settings, "GEMINI_TTS_DAILY_REQUEST_LIMIT", 0)

    decision = await quota.preflight(session, "gemini", MODEL, required_requests=500)

    assert decision.state is QuotaState.SUFFICIENT
    assert decision.limit is None


async def test_observed_exhaustion_beats_our_own_count(session, monkeypatch) -> None:
    """TEST 5 — the provider's own rejection is the one unarguable signal.

    Our count is a lower bound: it cannot see requests made in AI Studio or by
    another project on the same key. When the API says the day is spent, that
    settles it regardless of what the counter says.
    """
    monkeypatch.setattr(quota.settings, "GEMINI_TTS_DAILY_REQUEST_LIMIT", 10)
    await _spend(session, used=0, exhausted=True)

    decision = await quota.preflight(session, "gemini", MODEL, required_requests=1)

    assert decision.state is QuotaState.INSUFFICIENT
    assert "exhausted" in decision.reason


async def test_observed_limit_overrides_configuration(session, monkeypatch) -> None:
    """The tier we are actually on beats the tier someone typed into .env."""
    monkeypatch.setattr(quota.settings, "GEMINI_TTS_DAILY_REQUEST_LIMIT", 1000)
    await _spend(session, used=8, limit=10)

    decision = await quota.preflight(session, "gemini", MODEL, required_requests=5)

    assert decision.state is QuotaState.INSUFFICIENT
    assert decision.limit == 10


async def test_unmetered_provider_is_never_blocked(session) -> None:
    """Kokoro runs locally; none of this applies to it."""
    decision = await quota.preflight(session, "kokoro", None, required_requests=99)
    assert decision.state is QuotaState.SUFFICIENT


async def test_yesterdays_usage_does_not_count_against_today(session, monkeypatch) -> None:
    monkeypatch.setattr(quota.settings, "GEMINI_TTS_DAILY_REQUEST_LIMIT", 10)
    session.add(
        ProviderQuotaUsage(
            provider="gemini", model=MODEL,
            usage_date=quota.quota_day() - timedelta(days=1),
            requests_used=10,
        )
    )
    await session.flush()

    decision = await quota.preflight(session, "gemini", MODEL, required_requests=5)
    assert decision.allowed is True


# ------------------------------------------------------------- the accounting ---
async def test_requests_are_counted(session) -> None:
    await quota.record_request("gemini", MODEL)
    await quota.record_request("gemini", MODEL)

    snapshot = await quota.read_usage(session, "gemini", MODEL)
    assert snapshot.used == 2


async def test_counting_is_atomic_across_concurrent_writers(session) -> None:
    """Two renders overlapping must not lose an increment to a read-modify-write."""
    import asyncio

    await asyncio.gather(*(quota.record_request("gemini", MODEL) for _ in range(8)))

    snapshot = await quota.read_usage(session, "gemini", MODEL)
    assert snapshot.used == 8


async def test_unmetered_requests_are_not_counted(session) -> None:
    await quota.record_request("kokoro", "kokoro-v1.0")
    rows = (await session.execute(select(ProviderQuotaUsage))).scalars().all()
    assert rows == []


async def test_marking_exhausted_pins_the_day_shut(session) -> None:
    await quota.record_request("gemini", MODEL)
    await quota.mark_exhausted("gemini", MODEL, observed_limit=10)

    snapshot = await quota.read_usage(session, "gemini", MODEL)
    assert snapshot.exhausted is True
    assert snapshot.available == 0


async def test_a_failed_count_never_breaks_narration(monkeypatch) -> None:
    """Losing a counter increment is bad; failing a render over it is worse."""
    def boom(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr("app.db.session.session_scope", boom)
    await quota.record_request("gemini", MODEL)  # must not raise


# ------------------------------------------------ the guarantee, end to end ---
async def test_blocked_render_makes_zero_synthesis_calls(
    session, monkeypatch, tmp_path
) -> None:
    """TESTS 2, 3 and 7 — no Gemini calls, no Kokoro calls, no partial audio."""
    monkeypatch.setattr(quota.settings, "GEMINI_TTS_DAILY_REQUEST_LIMIT", 10)
    monkeypatch.setattr(production.settings, "TTS_PROVIDER", "gemini")
    await _spend(session, used=8)  # 2 available, 5 required

    project = await _project_ready_to_render(session)

    primary = _RecordingTTS("gemini")
    fallback = _RecordingTTS("kokoro-onnx")
    monkeypatch.setattr(production, "ResilientTTS", lambda **kw: primary)
    monkeypatch.setattr(production, "build_provider", lambda name: fallback)
    monkeypatch.setattr(production, "project_dir", lambda pid: tmp_path / str(pid))

    with pytest.raises(QuotaBlockedError) as excinfo:
        await production.produce(session, project)

    assert primary.calls == [], "a blocked render must not call the primary"
    assert fallback.calls == [], "a blocked render must not call the fallback either"
    assert excinfo.value.detail["state"] == "INSUFFICIENT"
    assert excinfo.value.detail["required_requests"] == 5
    assert excinfo.value.detail["available_requests"] == 2
    assert excinfo.value.detail["discovered"] == "preflight"
    # Nothing was written to disk.
    assert not (tmp_path / str(project.id)).exists()


async def test_sufficient_quota_reaches_synthesis(session, monkeypatch, tmp_path) -> None:
    """The gate must open as well as close, or it is just an outage."""
    monkeypatch.setattr(quota.settings, "GEMINI_TTS_DAILY_REQUEST_LIMIT", 10)
    monkeypatch.setattr(production.settings, "TTS_PROVIDER", "gemini")
    await _spend(session, used=0)

    project = await _project_ready_to_render(session)
    primary = _RecordingTTS("gemini")
    monkeypatch.setattr(production, "ResilientTTS", lambda **kw: primary)
    monkeypatch.setattr(production, "project_dir", lambda pid: tmp_path / str(pid))

    # _RecordingTTS asserts on call, which is how we know the gate opened.
    with pytest.raises(AssertionError, match="must not be reached"):
        await production.produce(session, project)
    assert len(primary.calls) == 1


async def test_preflight_count_matches_what_the_renderer_would_ask_for(
    session, monkeypatch
) -> None:
    """The whole guarantee rests on these two numbers being the same one."""
    monkeypatch.setattr(production.settings, "TTS_PROVIDER", "gemini")
    project = await _project_ready_to_render(session)
    script = await production.current_script(session, project)

    plan, decision = await production.plan_and_preflight(session, script)

    assert plan.request_count == decision.required_requests
    assert plan.request_count == len(list(script.scenes))


async def test_mid_render_exhaustion_blocks_instead_of_switching_voice(
    session, monkeypatch, tmp_path
) -> None:
    """TEST 5/7 — quota exhaustion never produces a fallback-narrated video.

    If the allowance is spent by something outside this system, the preflight
    cannot have known. The render still must not answer by re-reading the whole
    script in a voice nobody chose.
    """
    monkeypatch.setattr(quota.settings, "GEMINI_TTS_DAILY_REQUEST_LIMIT", 10)
    monkeypatch.setattr(production.settings, "TTS_PROVIDER", "gemini")
    await _spend(session, used=0)

    project = await _project_ready_to_render(session)
    script = await production.current_script(session, project)

    class _Exhausts(_RecordingTTS):
        async def synthesize(self, text, voice, out_path):
            self.calls.append(text)
            raise QuotaExhaustedError("daily quota spent")

    primary = _Exhausts("gemini")
    fallback = _RecordingTTS("kokoro-onnx")
    monkeypatch.setattr(production, "ResilientTTS", lambda **kw: primary)
    monkeypatch.setattr(production, "build_provider", lambda name: fallback)
    monkeypatch.setattr(production, "project_dir", lambda pid: tmp_path / str(pid))

    with pytest.raises(QuotaBlockedError) as excinfo:
        await production.synthesize_narration(session, project, script)

    assert len(primary.calls) == 1, "no retries against a spent daily quota"
    assert fallback.calls == [], "quota exhaustion must not fall back to another voice"
    assert excinfo.value.detail["discovered"] == "mid-render"


async def test_transient_failure_still_falls_back(session, monkeypatch, tmp_path) -> None:
    """TEST 6 — the existing behaviour for genuine transients is untouched."""
    monkeypatch.setattr(production.settings, "TTS_PROVIDER", "gemini")
    monkeypatch.setattr(production.settings, "TTS_FALLBACK_PROVIDER", "kokoro")

    project = await _project_ready_to_render(session)
    script = await production.current_script(session, project)

    class _Flaky(_RecordingTTS):
        async def synthesize(self, text, voice, out_path):
            self.calls.append(text)
            raise RetryableError("503 from the service")

    class _Works(_RecordingTTS):
        prefers_whole_block = False

        async def synthesize(self, text, voice, out_path):
            from app.providers.base import AudioResult

            self.calls.append(text)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"")
            return AudioResult(
                path=out_path, duration_seconds=1.0, sample_rate=24000,
                provider="kokoro-onnx", voice="am_puck",
            )

    primary, fallback = _Flaky("gemini"), _Works("kokoro-onnx")
    monkeypatch.setattr(production, "ResilientTTS", lambda **kw: primary)
    monkeypatch.setattr(production, "build_provider", lambda name: fallback)
    monkeypatch.setattr(production, "project_dir", lambda pid: tmp_path / str(pid))
    monkeypatch.setattr(production, "_prepare_block", _fixed_duration)
    monkeypatch.setattr(production, "_concat_segments", _noop_concat)

    parts = await production.synthesize_narration(session, project, script)

    assert fallback.calls, "a transient failure should still reach the fallback"
    voices = {(p.provenance or {}).get("provider") for p in parts}
    assert voices == {"kokoro-onnx"}, "and the result must still have one voice"


async def _fixed_duration(path) -> float:
    return 2.0


async def _noop_concat(parts, out_path, tail: float = 0.0) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"")


# ------------------------------------------------------------- the job runner ---
async def test_blocked_job_leaves_the_project_ready_not_failed(
    session, monkeypatch
) -> None:
    """A spent allowance is not a broken project; it is one to try tomorrow."""
    from app.jobs.tasks.content import produce_video

    monkeypatch.setattr(quota.settings, "GEMINI_TTS_DAILY_REQUEST_LIMIT", 10)
    monkeypatch.setattr(production.settings, "TTS_PROVIDER", "gemini")
    await _spend(session, used=10)

    project = await _project_ready_to_render(session)

    class _Ctx:
        def __init__(self, session):
            self.session = session
            self.job_id = None
            self.logger = __import__("app.core.logging", fromlist=["get_logger"]).get_logger("t")
            self.payload = {"project_id": project.id}

        def require(self, key):
            return self.payload[key]

    result = await produce_video(_Ctx(session))

    assert result["status"] == "BLOCKED"
    assert result["reason"] == "TTS_QUOTA_INSUFFICIENT"
    assert result["required_requests"] == 5
    assert result["available_requests"] == 0
    assert result["advanced_to_review"] is False
    # The project never entered RENDERING and is not marked failed.
    assert project.status == ProjectStatus.ASSETS_READY
