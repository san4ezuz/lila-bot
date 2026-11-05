"""Re-encodes the cell art in data/cell_images/ for Telegram: at most 1024 px, progressive JPEG.

Telegram recompresses every photo anyway, so a 1–7 MB source only makes the first upload of each
cell slower and the Docker image bigger. After the first send the bot reuses Telegram's file_id
(src/bot/cell_images.py) and uploads nothing.

    python scripts/compress_cell_images.py          # rewrite in place
    python scripts/compress_cell_images.py --check  # list files above the size target, change nothing

Run it after adding new art. Re-running on already compressed files only loses a little more quality,
so files already under the target are skipped.
"""

import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

IMAGES_DIR = Path(__file__).resolve().parents[1] / "data" / "cell_images"
MAX_SIDE = 1024
TARGET_BYTES = 260_000
QUALITIES = (84, 80, 76, 72, 68)


def encode(image: Image.Image, quality: int) -> bytes:
    buffer = BytesIO()
    image.save(buffer, "JPEG", quality=quality, optimize=True, progressive=True, subsampling="4:2:0")
    return buffer.getvalue()


def compress(path: Path) -> tuple[int, int]:
    """Returns (old size, new size). Steps quality down until the file fits the target."""
    old_size = path.stat().st_size
    with Image.open(path) as source:
        image = source.convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    for quality in QUALITIES:
        data = encode(image, quality)
        if len(data) <= TARGET_BYTES:
            break
    target = path.with_suffix(".jpg")
    target.write_bytes(data)
    if target != path:
        path.unlink()
    return old_size, len(data)


def main() -> None:
    check_only = "--check" in sys.argv
    total_before = total_after = 0
    for path in sorted(IMAGES_DIR.iterdir(), key=lambda p: p.name):
        if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
            continue
        size = path.stat().st_size
        if size <= TARGET_BYTES:
            total_before += size
            total_after += size
            continue
        if check_only:
            print(f"{path.name}: {size // 1024} KB")
            continue
        before, after = compress(path)
        total_before += before
        total_after += after
        print(f"{path.name}: {before // 1024} KB -> {after // 1024} KB")
    if not check_only:
        print(f"total: {total_before / 1e6:.1f} MB -> {total_after / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
