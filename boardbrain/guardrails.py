from __future__ import annotations
import re
from typing import Iterable, Dict, Any

_BOARD_SPECIFIC_PATTERNS = [
    r"\bpin\b", r"\bball\b", r"\bpad\b", r"\bnet\b", r"\brail\b",
    r"\bpp[a-z0-9_]+\b",  # PPBUS, PP3V3, etc.
    r"\bU\d+\b", r"\bQ\d+\b", r"\bL\d+\b", r"\bC\d+\b", r"\bR\d+\b", r"\bD\d+\b",
    r"\bCD32\d+\b", r"\bPMIC\b", r"\bSMC\b",
]

def is_board_specific_question(text: str) -> bool:
    t = text.lower()
    return any(re.search(p, t) for p in _BOARD_SPECIFIC_PATTERNS)

def has_required_evidence(attachments: Iterable[Dict[str, Any]]) -> bool:
    """True if we have at least one artifact that can serve as *truth*.

    A raw boardview file is useful for storage/opening in FlexBV, but the
    assistant cannot reliably extract net/pin truth from an opaque binary.
    For board-specific facts we therefore require a schematic PDF/page OR a
    boardview *screenshot* of the relevant area.
    """
    for a in attachments:
        t = (a.get("type") or "").lower()
        # We require an image-based artifact (schematic page screenshot or boardview screenshot)
        # because those can be directly interpreted in the current case context.
        if t in ("schematic", "boardview_screenshot"):
            return True
    return False

def refusal_message_missing_evidence() -> str:
    return (
        "I can't state board-specific facts (net/pin/rail expectations) without schematic/boardview evidence.\n\n"
        "Please upload ONE of the following to this case:\n"
        "- a FlexBV boardview screenshot showing the component/pad/net\n"
        "- a schematic page/screenshot of the relevant section\n\n"
        "If your schematic is a selectable-text PDF, you can also store it in KB_RAW_DIR (e.g. kb_raw/MacBook/A2338/820-02020/schematic.pdf) and run ingest; then I can cite it by page.\n\n"
        "Then ask again. I will treat the uploaded schematic/boardview as the source of truth."
    )