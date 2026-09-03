"""Animated caption generation as an ASS subtitle file.

Why ASS rather than FFmpeg `drawtext`: libass handles per-word timing,
outlines, shadows and positioning natively. Building the same effect from
chained `drawtext` filters is where FFmpeg caption pipelines become
unmaintainable (ARCH §14.3).

Style targets Shorts: large, high-contrast, short phrase chunks, positioned
clear of the Shorts UI overlays. The active word is highlighted in the
SystemDecoded cyan so the caption reads as ours rather than as a copy of
another creator's look.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.providers.base import WordTiming

# ASS colours are &HAABBGGRR (alpha, then BGR) — not RGB. Easy to get backwards.
CYAN_BGR = "&H00EED322"
WHITE_BGR = "&H00FAFAF8"
OUTLINE_BGR = "&H00140A04"
SHADOW_BGR = "&HA0000000"

DEFAULT_MAX_CHARS = 26
DEFAULT_MAX_WORDS = 4
# Bottom of the caption block sits above the Shorts description overlay.
DEFAULT_MARGIN_V = 430

# The ASS spec requires these header lines verbatim; kept as constants so the
# f-string below stays inside the line limit.
_STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
    "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
    "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
)
_EVENT_FORMAT = (
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
)


@dataclass(frozen=True, slots=True)
class CaptionChunk:
    words: list[WordTiming]

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.word for w in self.words)


def _fmt_time(seconds: float) -> str:
    """ASS timestamps are H:MM:SS.cc (centiseconds)."""
    seconds = max(0.0, seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def chunk_words(
    words: list[WordTiming],
    max_words: int = DEFAULT_MAX_WORDS,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_gap: float = 0.55,
) -> list[CaptionChunk]:
    """Group words into short phrases.

    Short chunks, not sentences: a wall of text on a phone is unreadable and
    covers the visuals. Breaks are forced at sentence-ending punctuation and at
    natural pauses in the measured audio, so captions track how the line is
    actually spoken.
    """
    chunks: list[CaptionChunk] = []
    current: list[WordTiming] = []

    for word in words:
        prospective = [*current, word]
        text_len = len(" ".join(w.word for w in prospective))
        gap = word.start - current[-1].end if current else 0.0

        too_long = len(prospective) > max_words or text_len > max_chars
        pause = bool(current) and gap > max_gap

        if current and (too_long or pause):
            chunks.append(CaptionChunk(words=current))
            current = [word]
        else:
            current = prospective

        # A sentence ending is a hard break regardless of length.
        if current and current[-1].word.rstrip().endswith((".", "!", "?")):
            chunks.append(CaptionChunk(words=current))
            current = []

    if current:
        chunks.append(CaptionChunk(words=current))
    return chunks


def _style_line(font: str, font_size: int, margin_v: int) -> str:
    """The single Style row. Positional and unforgiving — see the ASS spec."""
    return (
        f"Style: SD,{font},{font_size},{WHITE_BGR},{CYAN_BGR},{OUTLINE_BGR},{SHADOW_BGR},"
        f"1,0,0,0,100,100,1,0,1,7,4,2,90,90,{margin_v},1"
    )


def build_ass(
    words: list[WordTiming],
    *,
    width: int = 1080,
    height: int = 1920,
    font: str = "Inter",
    font_size: int = 74,
    margin_v: int = DEFAULT_MARGIN_V,
    highlight_active_word: bool = True,
    max_words: int = DEFAULT_MAX_WORDS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """Render word timings into an ASS subtitle document."""
    chunks = chunk_words(words, max_words=max_words, max_chars=max_chars)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
{_STYLE_FORMAT}
{_style_line(font, font_size, margin_v)}

[Events]
{_EVENT_FORMAT}
"""

    lines: list[str] = []
    for chunk in chunks:
        if not highlight_active_word:
            body = _escape(chunk.text)
            lines.append(
                f"Dialogue: 0,{_fmt_time(chunk.start)},{_fmt_time(chunk.end)},SD,,0,0,0,,{body}"
            )
            continue

        # One event per word, each showing the whole chunk with that word
        # highlighted. That is how the caption "follows" the voice.
        for index, word in enumerate(chunk.words):
            start = word.start
            end = chunk.words[index + 1].start if index + 1 < len(chunk.words) else chunk.end
            if end <= start:
                end = start + 0.08

            parts = []
            for j, w in enumerate(chunk.words):
                token = _escape(w.word)
                if j == index:
                    parts.append(f"{{\\c{CYAN_BGR}}}{token}{{\\c{WHITE_BGR}}}")
                else:
                    parts.append(token)
            body = " ".join(parts)
            lines.append(
                f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(end)},SD,,0,0,0,,{body}"
            )

    return header + "\n".join(lines) + "\n"
