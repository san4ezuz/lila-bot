"""Draws a finished game's share card (docs/ROADMAP.md, этап 4): the path over the 72-cell board,
the key cell with its art, a line about it and the bot's address. Two formats: `story` 9:16 for
Stories, `square` 1:1 for feeds and chats.

Pure presentation over CardDataResponse — what goes on the card is decided by the API
(src/services/card_service.py). Rendering takes ~0.2 s; call it through asyncio.to_thread.
Fonts: PT Serif and PT Sans (data/fonts/, SIL Open Font License).
"""

import math
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from src.api.schemas import CardDataResponse
from src.bot.cell_images import get_cell_image_path

CardFormat = Literal["story", "square"]

_FONTS_DIR = Path(__file__).resolve().parents[2] / "data" / "fonts"

# The palette of the cell art: parchment, maroon border, gold ornament.
_PAPER = (241, 230, 204)
_PAPER_EDGE = (222, 204, 166)
_MAROON = (122, 31, 43)
_GOLD = (184, 140, 46)
_INK = (58, 34, 30)
_MUTED = (125, 98, 80)
_CELL = (247, 239, 220)
_VISITED = (233, 205, 140)
_ARROW = (46, 110, 74)
_SNAKE = (150, 40, 40)

_COLUMNS = 9
_ROWS = 8

SIZES: dict[CardFormat, tuple[int, int]] = {"story": (1080, 1920), "square": (1080, 1080)}


@lru_cache
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_FONTS_DIR / name), size)


def _serif(size: int) -> ImageFont.FreeTypeFont:
    return _font("PT_Serif-Web-Regular.ttf", size)


def _serif_bold(size: int) -> ImageFont.FreeTypeFont:
    return _font("PT_Serif-Web-Bold.ttf", size)


def _serif_italic(size: int) -> ImageFont.FreeTypeFont:
    return _font("PT_Serif-Web-Italic.ttf", size)


def _sans(size: int) -> ImageFont.FreeTypeFont:
    return _font("PT_Sans-Web-Regular.ttf", size)


def _wrap(text: str, font: ImageFont.FreeTypeFont, width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and font.getlength(last + "…") > width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    center_x: int | None,
    left: int,
    top: int,
    spacing: float = 1.3,
) -> int:
    """Returns the y below the last line."""
    step = int(font.size * spacing)
    for i, line in enumerate(lines):
        y = top + i * step
        if center_x is not None:
            draw.text((center_x, y), line, font=font, fill=fill, anchor="ma")
        else:
            draw.text((left, y), line, font=font, fill=fill)
    return top + len(lines) * step


def _cell_center(cell_id: int, left: int, top: int, size: int) -> tuple[float, float]:
    """The Leela board: cell 1 bottom-left, rows of nine snaking upwards."""
    row = (cell_id - 1) // _COLUMNS
    col = (cell_id - 1) % _COLUMNS
    if row % 2 == 1:
        col = _COLUMNS - 1 - col
    x = left + col * size + size / 2
    y = top + (_ROWS - 1 - row) * size + size / 2
    return x, y


def _curve(
    start: tuple[float, float], end: tuple[float, float], bend: float
) -> list[tuple[float, float]]:
    (x1, y1), (x2, y2) = start, end
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1.0
    cx, cy = mx - dy / length * bend, my + dx / length * bend
    points = []
    for i in range(25):
        t = i / 24
        points.append(
            (
                (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t**2 * x2,
                (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t**2 * y2,
            )
        )
    return points


def _draw_board(
    image: Image.Image, data: CardDataResponse, left: int, top: int, size: int
) -> None:
    draw = ImageDraw.Draw(image)
    visited = set(data.path)
    number_font = _sans(max(14, size // 4))
    gap = max(2, size // 30)

    for cell_id in range(1, _COLUMNS * _ROWS + 1):
        x, y = _cell_center(cell_id, left, top, size)
        half = size / 2 - gap
        box = (x - half, y - half, x + half, y + half)
        fill = _VISITED if cell_id in visited else _CELL
        draw.rounded_rectangle(box, radius=size // 8, fill=fill, outline=_PAPER_EDGE, width=1)
        color = _INK if cell_id in visited else _MUTED
        draw.text((x - half + gap * 2, y - half + gap), str(cell_id), font=number_font, fill=color)

    # The path on its own layer, so the line can be soft without blurring the cells.
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    path_draw = ImageDraw.Draw(layer)
    jumps = {(j.from_cell, j.to_cell): j.kind for j in data.jumps}
    width = max(3, size // 14)
    for start, end in zip(data.path, data.path[1:], strict=False):
        a = _cell_center(start, left, top, size)
        b = _cell_center(end, left, top, size)
        kind = jumps.get((start, end))
        if kind is None:
            path_draw.line([a, b], fill=(*_MAROON, 170), width=width, joint="curve")
            continue
        color = _ARROW if kind == "arrow" else _SNAKE
        points = _curve(a, b, size * 0.6 if kind == "arrow" else -size * 0.6)
        path_draw.line(points, fill=(*color, 230), width=width + 2, joint="curve")
        tip = points[-1]
        path_draw.ellipse(
            (tip[0] - width * 1.6, tip[1] - width * 1.6, tip[0] + width * 1.6, tip[1] + width * 1.6),
            fill=(*color, 255),
        )
    if data.path:
        sx, sy = _cell_center(data.path[0], left, top, size)
        ex, ey = _cell_center(data.path[-1], left, top, size)
        r = size * 0.2
        path_draw.ellipse((sx - r, sy - r, sx + r, sy + r), outline=(*_MAROON, 255), width=width)
        path_draw.ellipse((ex - r, ey - r, ex + r, ey + r), fill=(*_MAROON, 255))
    kx, ky = _cell_center(data.key_cell.id, left, top, size)
    half = size / 2 - gap / 2
    path_draw.rounded_rectangle(
        (kx - half, ky - half, kx + half, ky + half),
        radius=size // 8,
        outline=(*_GOLD, 255),
        width=max(3, size // 16),
    )
    image.alpha_composite(layer)


def _paper(width: int, height: int) -> Image.Image:
    image = Image.new("RGBA", (width, height), (*_PAPER, 255))
    # A soft darker edge, like the parchment of the cell art.
    vignette = Image.new("L", (width, height), 0)
    ImageDraw.Draw(vignette).rectangle((0, 0, width, height), outline=255, width=width // 14)
    vignette = vignette.filter(ImageFilter.GaussianBlur(width // 16))
    edge = Image.new("RGBA", (width, height), (*_PAPER_EDGE, 255))
    image = Image.composite(edge, image, vignette)
    draw = ImageDraw.Draw(image)
    margin = width // 36
    draw.rectangle(
        (margin, margin, width - margin, height - margin), outline=_MAROON, width=width // 90
    )
    inner = margin + width // 60
    draw.rectangle((inner, inner, width - inner, height - inner), outline=_GOLD, width=2)
    return image


def _cell_art(cell_id: int, size: int) -> Image.Image | None:
    path = get_cell_image_path(cell_id)
    if path is None:
        return None
    with Image.open(path) as source:
        art = source.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size, size), radius=size // 14, fill=255)
    framed = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    framed.paste(art, (0, 0), mask)
    return framed


def _paste_art(image: Image.Image, cell_id: int, left: int, top: int, size: int) -> None:
    art = _cell_art(cell_id, size)
    draw = ImageDraw.Draw(image)
    border = max(4, size // 60)
    draw.rounded_rectangle(
        (left - border, top - border, left + size + border, top + size + border),
        radius=size // 14 + border,
        fill=_GOLD,
    )
    if art is None:
        draw.rounded_rectangle((left, top, left + size, top + size), radius=size // 14, fill=_CELL)
        draw.text(
            (left + size / 2, top + size / 2),
            str(cell_id),
            font=_serif_bold(size // 3),
            fill=_MAROON,
            anchor="mm",
        )
        return
    image.alpha_composite(art, (left, top))


def _subtitle(data: CardDataResponse) -> str:
    if data.reached_finish:
        return f"путь до клетки 68 · ходов: {data.turns}"
    return f"мой путь · ходов: {data.turns}"


def _footer(bot_username: str) -> str:
    return f"Сыграть: t.me/{bot_username}" if bot_username else "Игра «Лила» в Telegram"


def _render_story(data: CardDataResponse, with_intention: bool, bot_username: str) -> Image.Image:
    width, height = SIZES["story"]
    image = _paper(width, height)
    draw = ImageDraw.Draw(image)
    cx = width // 2

    draw.text((cx, 140), "Л И Л А", font=_serif_bold(84), fill=_MAROON, anchor="ma")
    draw.text((cx, 240), _subtitle(data), font=_sans(36), fill=_MUTED, anchor="ma")

    size = 90
    board_top = 310
    board_left = (width - size * _COLUMNS) // 2
    _draw_board(image, data, board_left, board_top, size)

    y = board_top + size * _ROWS + 44
    art = 330
    _paste_art(image, data.key_cell.id, cx - art // 2, y, art)
    y += art + 36

    title = f"Клетка {data.key_cell.id} — {data.key_cell.name}"
    y = _draw_lines(draw, _wrap(title, _serif_bold(48), 900, 2), _serif_bold(48), _MAROON, cx, 0, y)
    y += 10
    insight = _wrap(f"«{data.insight}»", _serif_italic(40), 880, 2)
    y = _draw_lines(draw, insight, _serif_italic(40), _INK, cx, 0, y)
    if data.question:
        y += 12
        y = _draw_lines(draw, _wrap(data.question, _sans(34), 860, 1), _sans(34), _MUTED, cx, 0, y)
    if with_intention and data.intention:
        y += 12
        intention = _wrap(f"Запрос: {data.intention}", _sans(32), 860, 2)
        _draw_lines(draw, intention, _sans(32), _MUTED, cx, 0, y)

    draw.text((cx, height - 112), _footer(bot_username), font=_sans(38), fill=_MAROON, anchor="ma")
    return image


def _render_square(data: CardDataResponse, with_intention: bool, bot_username: str) -> Image.Image:
    width, height = SIZES["square"]
    image = _paper(width, height)
    draw = ImageDraw.Draw(image)

    draw.text((80, 78), "ЛИЛА", font=_serif_bold(58), fill=_MAROON)
    draw.text((width - 80, 96), _subtitle(data), font=_sans(30), fill=_MUTED, anchor="ra")

    size = 60
    board_top = 180
    _draw_board(image, data, 80, board_top, size)

    art = 350
    art_left = width - 80 - art
    _paste_art(image, data.key_cell.id, art_left, board_top + (size * _ROWS - art) // 2, art)

    y = board_top + size * _ROWS + 40
    title = f"Клетка {data.key_cell.id} — {data.key_cell.name}"
    y = _draw_lines(draw, _wrap(title, _serif_bold(40), 920, 1), _serif_bold(40), _MAROON, None, 80, y)
    y += 4
    insight = _wrap(f"«{data.insight}»", _serif_italic(32), 920, 2)
    y = _draw_lines(draw, insight, _serif_italic(32), _INK, None, 80, y)
    if data.question:
        y += 6
        y = _draw_lines(draw, _wrap(data.question, _sans(28), 920, 1), _sans(28), _MUTED, None, 80, y)
    if with_intention and data.intention:
        y += 6
        intention = _wrap(f"Запрос: {data.intention}", _sans(28), 920, 1)
        _draw_lines(draw, intention, _sans(28), _MUTED, None, 80, y)

    draw.text((width - 80, height - 92), _footer(bot_username), font=_sans(30), fill=_MAROON, anchor="ra")
    return image


def render_card(
    data: CardDataResponse, format: CardFormat, with_intention: bool, bot_username: str
) -> bytes:
    """A JPEG of the card. `with_intention` prints the player's request — only on their request."""
    if format == "story":
        image = _render_story(data, with_intention, bot_username)
    else:
        image = _render_square(data, with_intention, bot_username)
    buffer = BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=90, optimize=True, progressive=True)
    return buffer.getvalue()
