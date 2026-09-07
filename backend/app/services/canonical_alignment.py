"""Map ASR timing onto the approved script.

The rule this module exists to enforce:

    The approved script is the source of truth for *what is said*.
    Speech recognition is evidence only for *when it was said*.

The Gemini auditions made the reason concrete. Whisper transcribed correct audio
as "That six-digit code in your authenticator app. Nobody sent it to you. Your
phone calculated it. That six-digit code in your authenticator app. That
six-digit code in your authenticator app." — the audio was right; the
transcription looped. It also invented "Not yet." out of nothing.

The previous implementation, on a token-count mismatch, kept the recognised
words. That is exactly backwards: it let a hallucinating transcriber rewrite
approved narration into the captions. Here, canonical tokens are never replaced.
They are aligned against the ASR sequence, take timings where a confident match
exists, and are interpolated where it does not. Recognised tokens with no
canonical counterpart — the hallucinations — are discarded.

Alignment is monotonic by construction: canonical word *n+1* can never start
before canonical word *n*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.logging import get_logger
from app.providers.base import WordTiming

log = get_logger("alignment.canonical")

__all__ = [
    "AlignmentConfidence",
    "AlignmentQuality",
    "CanonicalAlignment",
    "align_to_canonical",
    "normalise_token",
]

# Needleman-Wunsch scores. Matching is rewarded strongly enough that the
# alignment prefers skipping hallucinated ASR tokens over forcing bad pairings.
MATCH_SCORE = 3
MISMATCH_SCORE = -2
GAP_SCORE = -1

# Below this share of canonical words carrying a real matched timestamp, the
# ASR evidence is not trustworthy enough to time captions from.
LOW_COVERAGE = 0.55
WARN_COVERAGE = 0.75
# Recognised tokens with no canonical counterpart, as a share of ASR output.
HIGH_INSERTION_RATIO = 0.35


class AlignmentConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


def normalise_token(token: str) -> str:
    """Collapse a word to its comparable core.

    Punctuation, case and hyphenation differ freely between the script and what
    a recogniser emits ("six-digit" vs "six digit"), and none of it should
    prevent a match.
    """
    return re.sub(r"[^a-z0-9']", "", token.lower())


@dataclass(slots=True)
class AlignmentQuality:
    """Evidence about how well the ASR output matched the approved script."""

    canonical_words: int
    asr_words: int
    matched: int
    interpolated: int
    discarded_asr: int
    repeated_ngrams: int
    monotonicity_fixes: int
    confidence: AlignmentConfidence
    warnings: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.matched / self.canonical_words if self.canonical_words else 0.0

    @property
    def insertion_ratio(self) -> float:
        return self.discarded_asr / self.asr_words if self.asr_words else 0.0

    def as_dict(self) -> dict:
        return {
            "canonical_words": self.canonical_words,
            "asr_words": self.asr_words,
            "matched": self.matched,
            "interpolated": self.interpolated,
            "discarded_asr": self.discarded_asr,
            "repeated_ngrams": self.repeated_ngrams,
            "monotonicity_fixes": self.monotonicity_fixes,
            "coverage": round(self.coverage, 4),
            "insertion_ratio": round(self.insertion_ratio, 4),
            "confidence": self.confidence.value,
            "warnings": list(self.warnings),
        }


@dataclass(slots=True)
class CanonicalAlignment:
    """Approved words with timings. `words` is always the canonical script."""

    words: list[WordTiming]
    quality: AlignmentQuality


def _match_pairs(canonical: list[str], asr: list[str]) -> list[tuple[int, int]]:
    """Needleman-Wunsch global alignment; returns matched (canonical, asr) pairs.

    Only genuine equal-token matches are returned. Mismatched pairings are
    dropped rather than trusted, because a mismatch here usually means the
    recogniser heard something that is not in the script.
    """
    m, n = len(canonical), len(asr)
    if not m or not n:
        return []

    # score[i][j] = best score aligning canonical[:i] with asr[:j]
    score = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        score[i][0] = i * GAP_SCORE
    for j in range(1, n + 1):
        score[0][j] = j * GAP_SCORE

    for i in range(1, m + 1):
        ci = canonical[i - 1]
        row, prev = score[i], score[i - 1]
        for j in range(1, n + 1):
            diag = prev[j - 1] + (MATCH_SCORE if ci == asr[j - 1] else MISMATCH_SCORE)
            row[j] = max(diag, prev[j] + GAP_SCORE, row[j - 1] + GAP_SCORE)

    pairs: list[tuple[int, int]] = []
    i, j = m, n
    while i > 0 and j > 0:
        ci, aj = canonical[i - 1], asr[j - 1]
        diag = score[i - 1][j - 1] + (MATCH_SCORE if ci == aj else MISMATCH_SCORE)
        if score[i][j] == diag:
            if ci == aj:
                pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif score[i][j] == score[i - 1][j] + GAP_SCORE:
            i -= 1
        else:
            j -= 1

    pairs.reverse()
    return pairs


def _count_repeated_ngrams(tokens: list[str], size: int = 4) -> int:
    """Count n-grams occurring more than once — the signature of an ASR loop."""
    if len(tokens) < size:
        return 0
    seen: dict[tuple[str, ...], int] = {}
    for i in range(len(tokens) - size + 1):
        gram = tuple(tokens[i : i + size])
        seen[gram] = seen.get(gram, 0) + 1
    return sum(1 for count in seen.values() if count > 1)


def _distribute(
    canonical_tokens: list[str], start: float, end: float
) -> list[tuple[float, float]]:
    """Spread words across a span, weighted by length.

    The safe degradation when ASR evidence is untrustworthy: timings are
    approximate but always inside the real audio, always monotonic, and the text
    is still the approved script.
    """
    span = max(0.05, end - start)
    weights = [max(1, len(t)) for t in canonical_tokens]
    total = sum(weights) or 1
    out: list[tuple[float, float]] = []
    cursor = start
    for weight in weights:
        width = span * (weight / total)
        out.append((cursor, cursor + width))
        cursor += width
    return out


def align_to_canonical(
    canonical_text: str,
    asr_words: list[WordTiming],
    *,
    block_start: float = 0.0,
    block_end: float | None = None,
    label: str = "",
) -> CanonicalAlignment:
    """Give every approved word a timestamp, using ASR only as timing evidence.

    Returned `words` always spell the approved script — never the recogniser's
    version of it.
    """
    canonical_tokens = [t for t in canonical_text.split() if t.strip()]
    if not canonical_tokens:
        return CanonicalAlignment(
            words=[],
            quality=AlignmentQuality(0, len(asr_words), 0, 0, len(asr_words), 0, 0,
                                     AlignmentConfidence.LOW, ["empty canonical text"]),
        )

    audio_end = block_end if block_end is not None else (
        asr_words[-1].end if asr_words else block_start + 1.0
    )

    canonical_norm = [normalise_token(t) for t in canonical_tokens]
    asr_norm = [normalise_token(w.word) for w in asr_words]

    pairs = _match_pairs(canonical_norm, asr_norm)
    warnings: list[str] = []

    repeated = _count_repeated_ngrams(asr_norm)
    if repeated:
        warnings.append(
            f"ASR produced {repeated} repeated 4-gram(s) — likely transcription loop; "
            "canonical text preserved"
        )

    discarded = len(asr_words) - len(pairs)
    insertion_ratio = discarded / len(asr_words) if asr_words else 0.0
    if insertion_ratio > HIGH_INSERTION_RATIO:
        warnings.append(
            f"{discarded}/{len(asr_words)} recognised words had no counterpart in the "
            "script and were discarded"
        )

    coverage = len(pairs) / len(canonical_tokens)
    if coverage < LOW_COVERAGE:
        # Not enough trustworthy anchors. Distribute rather than invent.
        warnings.append(
            f"alignment coverage {coverage:.0%} below {LOW_COVERAGE:.0%}; "
            "timings distributed proportionally"
        )
        spans = _distribute(canonical_tokens, block_start, audio_end)
        words = [
            WordTiming(word=tok, start=s, end=e)
            for tok, (s, e) in zip(canonical_tokens, spans, strict=True)
        ]
        quality = AlignmentQuality(
            canonical_words=len(canonical_tokens), asr_words=len(asr_words),
            matched=len(pairs), interpolated=len(canonical_tokens) - len(pairs),
            discarded_asr=discarded, repeated_ngrams=repeated, monotonicity_fixes=0,
            confidence=AlignmentConfidence.LOW, warnings=warnings,
        )
        _log(label, quality)
        return CanonicalAlignment(words=words, quality=quality)

    # Anchor every matched canonical token, then fill the gaps by interpolating
    # between the anchors either side.
    starts: list[float | None] = [None] * len(canonical_tokens)
    ends: list[float | None] = [None] * len(canonical_tokens)
    for ci, ai in pairs:
        starts[ci], ends[ci] = asr_words[ai].start, asr_words[ai].end

    interpolated = _fill_gaps(starts, ends, block_start, audio_end)
    fixes = _enforce_monotonic(starts, ends, block_start, audio_end)
    if fixes:
        warnings.append(f"{fixes} timestamp(s) were out of order and were corrected")

    confidence = (
        AlignmentConfidence.HIGH
        if coverage >= WARN_COVERAGE and not repeated
        else AlignmentConfidence.MEDIUM
    )

    words = [
        WordTiming(word=tok, start=float(s), end=float(e))
        for tok, s, e in zip(canonical_tokens, starts, ends, strict=True)
    ]
    quality = AlignmentQuality(
        canonical_words=len(canonical_tokens), asr_words=len(asr_words),
        matched=len(pairs), interpolated=interpolated, discarded_asr=discarded,
        repeated_ngrams=repeated, monotonicity_fixes=fixes,
        confidence=confidence, warnings=warnings,
    )
    _log(label, quality)
    return CanonicalAlignment(words=words, quality=quality)


def _fill_gaps(
    starts: list[float | None], ends: list[float | None], lo: float, hi: float
) -> int:
    """Interpolate timings for canonical words the recogniser did not match."""
    n = len(starts)
    filled = 0
    i = 0
    while i < n:
        if starts[i] is not None:
            i += 1
            continue
        run_start = i
        while i < n and starts[i] is None:
            i += 1
        run_end = i  # exclusive

        left = ends[run_start - 1] if run_start > 0 and ends[run_start - 1] is not None else lo
        right = starts[run_end] if run_end < n and starts[run_end] is not None else hi
        if right <= left:
            right = left + 0.12 * (run_end - run_start)

        width = (right - left) / (run_end - run_start)
        for k in range(run_start, run_end):
            starts[k] = left + width * (k - run_start)
            ends[k] = left + width * (k - run_start + 1)
            filled += 1
    return filled


def _enforce_monotonic(
    starts: list[float | None], ends: list[float | None], lo: float, hi: float
) -> int:
    """Guarantee non-decreasing, non-overlapping, in-bounds timings.

    ASR timestamps can jump backwards, especially around a hallucinated repeat.
    Captions that jump backwards are worse than slightly imprecise ones.
    """
    fixes = 0
    cursor = lo
    for i in range(len(starts)):
        s, e = float(starts[i]), float(ends[i])
        if s < cursor:
            s = cursor
            fixes += 1
        if e <= s:
            e = s + 0.08
            fixes += 1
        s, e = min(s, hi), min(e, hi)
        if e <= s:
            e = min(hi, s + 0.05)
        starts[i], ends[i] = s, e
        cursor = e
    return fixes


def _log(label: str, quality: AlignmentQuality) -> None:
    payload = {
        "block": label,
        "coverage": round(quality.coverage, 3),
        "matched": quality.matched,
        "interpolated": quality.interpolated,
        "discarded_asr": quality.discarded_asr,
        "confidence": quality.confidence.value,
    }
    if quality.warnings:
        log.warning("alignment.canonical_warnings", **payload, warnings=quality.warnings)
    else:
        log.info("alignment.canonical", **payload)
