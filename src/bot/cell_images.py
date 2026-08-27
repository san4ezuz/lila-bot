"""Optional per-cell artwork. Lives under data/cell_images/{cell_id}.{ext} — a plain file-per-cell
convention rather than a DB/API-served field, since only the bot process ever needs these bytes
(to attach as a Telegram photo) and there's no reason to round-trip them through the Game API.
Not every cell needs an image — get_cell_image_path() returning None means "send plain text",
which is the default for any cell without a file here.
"""

from pathlib import Path

_IMAGES_DIR = Path(__file__).resolve().parents[2] / "data" / "cell_images"
_EXTENSIONS = ("jpg", "jpeg", "png", "webp")


def get_cell_image_path(cell_id: int) -> Path | None:
    for ext in _EXTENSIONS:
        candidate = _IMAGES_DIR / f"{cell_id}.{ext}"
        if candidate.exists():
            return candidate
    return None
