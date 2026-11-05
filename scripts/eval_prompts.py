"""Reference set for the guide prompt: fixed game situations run against the live model, with
automatic checks and a Markdown report to read through by eye.

    python scripts/eval_prompts.py [report.md]

Run it on every change to the system prompt, a context builder (src/llm/prompts.py) or
LLM_MODEL — on the production model (docs/PROMPTS.md). Costs a few cents: ~10 calls.

The checks catch the measurable failures — length over the prompt's word limits, lists and
markdown, a missing closing question, banned phrases, «(-а)» patches, a fallback. Tone and
whether a reading fits the request are for the human reading the report.
"""

import asyncio
import re
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import get_settings
from src.core.board import get_default_board
from src.llm import guide
from src.llm.client import LlmResult

BOARD = get_default_board()


def cell_label(cell_id: int) -> str:
    return f"{cell_id} {BOARD.get_cell(cell_id).name}"


@dataclass(frozen=True)
class Case:
    name: str
    word_limit: int
    run: Callable[[], Awaitable[LlmResult]]
    ends_with_question: bool = True
    # The gender the reply must use: None — not known, so no gendered forms at all.
    gender: str | None = None


CASES = [
    Case(
        "interpret: обычный ход, запрос «отношения»",
        120,
        lambda: guide.interpret_cell(
            intention="отношения — с близкими, с партнёром, с собой",
            cell=BOARD.get_cell(9),
            event="moved",
            transition=None,
            visited=[cell_label(6), cell_label(9)],
            recent_player_answers=[],
        ),
    ),
    Case(
        "interpret: змея, пропущенная клетка",
        120,
        lambda: guide.interpret_cell(
            intention="работа и дело — куда двигаться и что выбрать",
            cell=BOARD.get_cell(8),
            event="moved",
            transition="snake",
            skipped_cell=BOARD.get_cell(12),
            visited=[cell_label(6), cell_label(9), cell_label(8)],
            recent_player_answers=["я всё время откладываю решение"],
        ),
    ),
    Case(
        "interpret: стрела",
        120,
        lambda: guide.interpret_cell(
            intention="застой — хочется сдвинуться с места",
            cell=BOARD.get_cell(23),
            event="moved",
            transition="arrow",
            skipped_cell=BOARD.get_cell(10),
            visited=[cell_label(6), cell_label(23)],
            recent_player_answers=[],
        ),
    ),
    Case(
        "interpret: повтор клетки после змеи",
        120,
        lambda: guide.interpret_cell(
            intention="застой — хочется сдвинуться с места",
            cell=BOARD.get_cell(9),
            event="moved",
            transition=None,
            visited=[cell_label(6), cell_label(9), cell_label(11), cell_label(8), cell_label(9)],
            recent_player_answers=["снова то же самое"],
            revisit=True,
        ),
    ),
    Case(
        "interpret: финал",
        120,
        lambda: guide.interpret_cell(
            intention="отношения — с близкими, с партнёром, с собой",
            cell=BOARD.get_cell(68),
            event="finished",
            transition=None,
            visited=[cell_label(n) for n in (6, 9, 23, 41, 55, 68)],
            recent_player_answers=[],
        ),
        ends_with_question=False,
    ),
    Case(
        "deepen: женский род по словам игрока, с историей клетки",
        90,
        lambda: guide.deepen(
            "отношения — с близкими, с партнёром, с собой",
            BOARD.get_cell(9),
            "я устала всё время подстраиваться",
            dialogue=[
                (
                    "guide",
                    (
                        "Клетка о желаниях тела и удовольствии. Где в отношениях ты "
                        "позволяешь себе хотеть, а где сразу думаешь о другом?"
                    ),
                ),
            ],
        ),
        ends_with_question=False,
        gender="female",
    ),
    Case(
        "deepen: короткий ответ без рода",
        90,
        lambda: guide.deepen(
            "работа и дело — куда двигаться и что выбрать",
            BOARD.get_cell(14),
            "не знаю",
            dialogue=[
                (
                    "guide",
                    (
                        "На этой клетке видно, на что уходят силы. Что в работе забирает "
                        "у тебя больше всего энергии?"
                    ),
                ),
            ],
        ),
        ends_with_question=False,
    ),
    Case(
        "not_entered: последняя попытка перед входом",
        60,
        lambda: guide.not_entered_message(
            "застой — хочется сдвинуться с места", roll=3, attempt=3, guarantee_after=4
        ),
        ends_with_question=False,
    ),
    Case(
        "summary: ответы, змея, стрела, реакции",
        250,
        lambda: guide.summarize_game(
            "работа и дело — куда двигаться и что выбрать",
            [
                cell_label(6),
                cell_label(9),
                f"{cell_label(12)} —змея→ {cell_label(8)}",
                f"{cell_label(10)} —стрела→ {cell_label(23)}",
                cell_label(41),
                cell_label(68),
            ],
            player_answers=[
                "я всё время откладываю решение",
                "боюсь, что не потяну новое",
                "кажется, мне нужно разрешить себе ошибиться",
            ],
            resonated=[cell_label(8), cell_label(23)],
            not_resonated=[cell_label(9)],
        ),
    ),
]

_BANNED = ("вселенная", "отличный вопрос", "прекрасное наблюдение", "прекрасный вопрос")
_LIST_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s", re.MULTILINE)
_MARKDOWN = re.compile(r"\*\*|__|^#", re.MULTILINE)
_GENDER_PATCH = re.compile(r"\((?:-?а|-?ась)\)")
# Masculine forms addressed to the player: «ты попал», «ты сам», «ты скатился», «готов».
_MASCULINE = re.compile(
    r"\bты\s+(?:уже\s+|снова\s+|не\s+)?(?:сам|\w+л|\w+лся)\b|\bготов\b(?!о)", re.IGNORECASE
)
_ANNOUNCED_GENDER = re.compile(r"в (?:женском|мужском) роде|род уже понят", re.IGNORECASE)
_FORMAL_YOU = re.compile(r"\b(?:вы|вас|вам|вами|ваш\w*)\b", re.IGNORECASE)


def check(case: Case, result: LlmResult) -> list[str]:
    text = result.text
    problems = []
    if result.fallback:
        problems.append(f"фолбэк: {result.error}")
    words = len(text.split())
    if words > case.word_limit * 1.15:
        problems.append(f"длина {words} слов при лимите {case.word_limit}")
    if case.ends_with_question and not text.rstrip().endswith("?"):
        problems.append("не заканчивается вопросом")
    if text.count("?") > 2:
        problems.append(f"вопросов: {text.count('?')}, а нужен один")
    if _LIST_LINE.search(text):
        problems.append("список")
    if _MARKDOWN.search(text):
        problems.append("markdown")
    if _GENDER_PATCH.search(text):
        problems.append("форма вида «(-а)»")
    if case.gender != "male" and (match := _MASCULINE.search(text)):
        problems.append(f"мужской род: «{match.group(0)}»")
    if _ANNOUNCED_GENDER.search(text):
        problems.append("объявляет род вслух")
    if match := _FORMAL_YOU.search(text):
        problems.append(f"на «вы»: «{match.group(0)}»")
    lowered = text.lower()
    problems.extend(f"штамп «{phrase}»" for phrase in _BANNED if phrase in lowered)
    return problems


async def main() -> None:
    report_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("prompt_eval.md")
    model = get_settings().llm_model
    results = await asyncio.gather(*(case.run() for case in CASES))

    lines = [f"# Эталонный прогон промпта — {model}", ""]
    failed = 0
    for case, result in zip(CASES, results, strict=True):
        problems = check(case, result)
        failed += bool(problems)
        status = "✅" if not problems else "❌ " + "; ".join(problems)
        lines += [
            f"## {case.name}",
            "",
            (
                f"{status} · {len(result.text.split())} слов · "
                f"{result.input_tokens} in / {result.output_tokens} out · {result.duration_ms} мс"
            ),
            "",
            *[f"> {line}" if line else ">" for line in result.text.splitlines()],
            "",
        ]
    lines.insert(1, f"\nСлучаев: {len(CASES)}, с замечаниями: {failed}.\n")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(CASES) - failed}/{len(CASES)} без замечаний, отчёт: {report_path}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
