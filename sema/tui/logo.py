"""
The sema wordmark, in text.

The icon (``vscode-extension/media/icon-128.svg``) is an S built from two ribbon
strokes — a top stroke sweeping right and a bottom one sweeping left. Both the
mark and the spinner below keep that shape rather than falling back to a generic
block letter, so the terminal reads as the same product as the extension.
"""

from __future__ import annotations

# The two ribbons of the icon. Each is one slanted bar drawn across two rows —
# the lower row shifted left — which is what gives the parallelogram its lean.
MARK = [
    "   ▟██████▙",
    " ▟██████▛",
    "   ▟██████▙",
    " ▟██████▛",
]

# "sema" as a block wordmark, sized to sit beside the mark.
WORDMARK = [
    "▗▄▄▖ ▗▄▄▄▖▗▖  ▗▖ ▗▄▖ ",
    "▐▌   ▐▌   ▐▛▚▞▜▌▐▌ ▐▌",
    " ▝▀▚▖▐▛▀▀▘▐▌  ▐▌▐▛▀▜▌",
    "▗▄▄▞▘▐▙▄▄▖▐▌  ▐▌▐▌ ▐▌",
]

# Shown when the window is too narrow for the full lockup.
COMPACT = "▟▛ ▟▛  sema"

# One glyph for inline use — status bar, prompts, the running indicator.
GLYPH = "▚"

# Spinner frames: the two ribbons chasing each other, so a running turn still
# looks like sema rather than a generic throbber.
SPINNER = ["▚▘", "▚▖", "▞▖", "▞▘"]

# Width the full lockup needs, including the gap between mark and wordmark.
_GAP = 3


def full_width() -> int:
    mark = max(len(line) for line in MARK)
    word = max(len(line) for line in WORDMARK)
    return mark + _GAP + word


def render(width: int = 100) -> str:
    """The banner sized for the terminal: full lockup, or a compact fallback."""
    if width < full_width():
        return COMPACT
    mark_width = max(len(line) for line in MARK)
    # Bottom-align the wordmark against the mark so the baselines agree.
    pad_top = max(0, len(MARK) - len(WORDMARK))
    lines: list[str] = []
    for index in range(max(len(MARK), len(WORDMARK) + pad_top)):
        left = MARK[index] if index < len(MARK) else ""
        word_index = index - pad_top
        right = WORDMARK[word_index] if 0 <= word_index < len(WORDMARK) else ""
        lines.append(f"{left.ljust(mark_width)}{' ' * _GAP}{right}".rstrip())
    return "\n".join(lines)
