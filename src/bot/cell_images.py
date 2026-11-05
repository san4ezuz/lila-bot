"""Optional per-cell artwork. Lives under data/cell_images/{cell_id}.{ext} — a plain file-per-cell
convention rather than a DB/API-served field, since only the bot process ever needs these bytes
(to attach as a Telegram photo) and there's no reason to round-trip them through the Game API.
Not every cell needs an image — get_cell_image_path() returning None means "send plain text",
which is the default for any cell without a file here.

The files are compressed for Telegram (scripts/compress_cell_images.py). Each one is uploaded once
per process: after the first send Telegram's file_id is reused, so a roll sends a short string
instead of ~250 KB. The cache is in memory — a deploy costs one upload per cell, nothing more.
"""

from pathlib import Path

from aiogram.types import FSInputFile, Message

_IMAGES_DIR = Path(__file__).resolve().parents[2] / "data" / "cell_images"
_EXTENSIONS = ("jpg", "jpeg", "png", "webp")

_file_ids: dict[int, str] = {}


def get_cell_image_path(cell_id: int) -> Path | None:
    for ext in _EXTENSIONS:
        candidate = _IMAGES_DIR / f"{cell_id}.{ext}"
        if candidate.exists():
            return candidate
    return None


def cell_photo(cell_id: int) -> str | FSInputFile | None:
    """What to pass as `photo`: the cached file_id, the file itself, or None for a cell without art."""
    if cell_id in _file_ids:
        return _file_ids[cell_id]
    path = get_cell_image_path(cell_id)
    return FSInputFile(path) if path is not None else None


def remember_file_id(cell_id: int, sent: Message) -> None:
    if sent.photo:
        _file_ids[cell_id] = sent.photo[-1].file_id


def forget_file_id(cell_id: int) -> bool:
    """Drops a file_id Telegram refused. Returns True if there was one to drop."""
    return _file_ids.pop(cell_id, None) is not None
