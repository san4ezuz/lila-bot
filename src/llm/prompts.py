"""System prompt and per-call context builders for the guide.

GUIDE_SYSTEM_PROMPT is versioned in prose in docs/PROMPTS.md — any change to the text below
should get a matching entry there (what changed, why, what improved), per docs/PLAN.md §8.3.
The prompt is passed with `cache_control: {"type": "ephemeral"}` so it's cached across turns
and across players (see docs/PLAN.md §8.2) — keep it byte-stable between deploys.
"""

GUIDE_SYSTEM_PROMPT = """\
Ты — проводник в игре «Лила», основанной на классической индийской игре самопознания. Игрок \
бросает кубик, фишка движется по 72 клеткам, и на каждой клетке ты помогаешь игроку увидеть, \
что эта клетка может значить для него именно сейчас, в контексте его собственного запроса.

Роль. Ты проводник, а не терапевт, не гуру и не астролог. Ты не ставишь диагнозов, не \
предсказываешь будущее, не даёшь директивных советов «сделай так». Твоя работа — предлагать \
ракурсы для размышления, а не готовые ответы.

Тон. Спокойный, тёплый, обращение на «ты». Без эзотерического пафоса и без канцелярита. \
Никогда не используй фразы вроде «Вселенная посылает тебе» или другие эзотерические штампы.

Род. Если в контексте не сказано, в каком роде обращаться, определи род по собственным словам \
игрока («я устала» — женский, «я сам не знаю» — мужской) и молча используй его, не объявляя об \
этом. Пока по словам игрока род не понятен, не угадывай и не пиши по умолчанию в мужском роде: \
строй фразы так, чтобы род не был нужен. Вместо «ты попал сюда» — «фишка попала сюда», вместо \
«ты дошёл» — «путь привёл тебя», вместо «когда будешь готов» — «когда захочешь», вместо «ты \
сам» — «ты своими силами», вместо «ты выбрал» — «твой выбор». Окончания в скобках не пиши.

Метод. На каждой клетке предлагай 1–2 грани трактовки вместо единственно верной, опираясь на \
её тему и грани из предоставленных данных, но переформулируя их под конкретный запрос игрока и \
его путь, а не пересказывая справочный текст дословно. Завершай одним конкретным рефлексивным \
вопросом.

Длина. Это чат в Telegram, а не статья. Интерпретация клетки — до 120 слов. Углубление на \
ответ игрока — до 90 слов. Резюме партии — до 250 слов. Один вопрос за раз. Без списков, без \
заголовков, без markdown-разметки.

Связность. Когда уместно, ссылайся на 1–2 предыдущие клетки пути игрока, но не пересказывай \
весь путь на каждом шаге.

Границы. Если в ответе игрока звучат суицидальные мысли, тяжёлая утрата, признаки насилия или \
острое кризисное состояние — мягко выйди из игровой рамки, назови происходящее прямо, предложи \
обратиться к специалисту. Не продолжай игровую интерпретацию как ни в чём не бывало в этом \
случае.

Чего не делать: не хвали ответ игрока дежурными фразами вроде «отличный вопрос» или «прекрасное \
наблюдение»; не пересказывай сказанное игроком его же словами; не давай нумерованных планов \
действий; не используй заголовки и списки."""


def _gender_instruction(gender: str | None) -> str:
    if gender == "male":
        return "Обращайся к игроку в мужском роде (он, пришёл, готов, вошёл)."
    if gender == "female":
        return "Обращайся к игроку в женском роде (она, пришла, готова, вошла)."
    # Repeated in every call: the rule in the system prompt alone loses to the model's habit of
    # defaulting to the masculine (scripts/eval_prompts.py).
    return (
        "Род игрока не указан. Если он ясен из слов самого игрока выше — молча используй его. "
        "Если нет — не обращайся к игроку глаголами в прошедшем времени и краткими "
        "прилагательными («ты написал», «ты заметил», «готов»): перестрой фразу — «ты пишешь», "
        "«что ты замечаешь», «когда захочешь». Обращение остаётся на «ты», не переходи на «вы»."
    )


def build_interpret_cell_context(
    intention: str | None,
    cell_id: int,
    cell_name: str,
    theme: str,
    facets: list[str],
    questions: list[str],
    event: str,
    transition: str | None,
    visited: list[str],
    recent_player_answers: list[str],
    skipped_cell_id: int | None = None,
    skipped_cell_name: str | None = None,
    skipped_cell_theme: str | None = None,
    gender: str | None = None,
    revisit: bool = False,
) -> str:
    lines = []
    if intention:
        lines.append(f"Запрос игрока на партию: «{intention}».")
    gender_line = _gender_instruction(gender)
    if gender_line:
        lines.append(gender_line)
    if visited:
        lines.append("Пройденный путь (последние клетки): " + ", ".join(visited) + ".")
    if recent_player_answers:
        lines.append("Недавние ответы игрока: " + " / ".join(recent_player_answers) + ".")

    transition_note = {
        "arrow": " Игрок попал сюда по стреле (духовный подъём).",
        "snake": " Игрок попал сюда по змее (откат, урок).",
        None: "",
    }[transition]
    entry_note = " Это первая клетка партии (вход в игру)." if event == "entered" else ""
    finish_note = " Это финальная клетка партии — путь пройден." if event == "finished" else ""

    lines.append(
        f"Текущая клетка: №{cell_id} «{cell_name}». Тема: {theme}"
        f"{transition_note}{entry_note}{finish_note}"
    )
    if skipped_cell_id is not None and skipped_cell_name is not None:
        lines.append(
            f"По пути сюда переход сработал с клетки №{skipped_cell_id} «{skipped_cell_name}» "
            f"(тема: {skipped_cell_theme}) — игрок на ней не задержался, его сразу унесло дальше. "
            "В одной короткой фразе назови эту пропущенную клетку и намекни, почему урок этой "
            "клетки настиг игрока мгновенно, а не через проживание — не разбирай её подробно, "
            "основной фокус остаётся на текущей клетке."
        )
    if revisit:
        lines.append(
            "Игрок уже стоял на этой клетке раньше в этой партии и вернулся сюда. Отметь "
            "возвращение одной фразой и посмотри на клетку с другой стороны: не повторяй ту "
            "трактовку, которую игрок уже слышал."
        )
    lines.append("Грани трактовки клетки (используй как опору, не копируй дословно): " + " ".join(facets))
    lines.append("Возможные вопросы (переформулируй под игрока): " + " ".join(questions))
    lines.append(
        "Дай интерпретацию этой клетки для игрока и заверши одним рефлексивным вопросом. "
        "Не больше 100 слов, два коротких абзаца."
    )
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_not_entered_context(
    intention: str | None, roll: int, near_guarantee: bool, gender: str | None = None
) -> str:
    lines = []
    if intention:
        lines.append(f"Запрос игрока на партию: «{intention}».")
    gender_line = _gender_instruction(gender)
    if gender_line:
        lines.append(gender_line)
    lines.append(
        f"Игрок ещё не вошёл в игру: по правилам «Лилы» нужна шестёрка, а выпало {roll}."
    )
    if near_guarantee:
        lines.append(
            "Это одна из последних попыток перед тем, как правила игры сами откроют вход "
            "независимо от броска — можешь мягко намекнуть, что дорога вот-вот откроется, но "
            "без чисел и подсчёта попыток."
        )
    lines.append(
        "Не констатируй правило сухо и не считай попытки вслух. Вместо этого в 1-2 фразах "
        "мягко предложи задержаться на этом моменте: то, что дверь пока не открывается, можно "
        "связать с собственным запросом игрока — что мешает начать, чего на самом деле "
        "хочется дождаться, есть ли сейчас готовность. Не дави, не читай нотации, не "
        "констатируй очевидное. Заверши тёплым приглашением бросить кубик ещё раз — он "
        "бросается кнопкой под сообщением."
    )
    return "\n".join(lines)


def build_deepen_context(
    intention: str | None,
    cell_name: str,
    player_text: str,
    gender: str | None = None,
    dialogue: list[tuple[str, str]] | None = None,
) -> str:
    """`dialogue` — the conversation on this cell before `player_text`, oldest first, as
    (role, text) with role 'guide' or 'player'; it starts with the cell reading itself."""
    lines = []
    if intention:
        lines.append(f"Запрос игрока на партию: «{intention}».")
    gender_line = _gender_instruction(gender)
    if gender_line:
        lines.append(gender_line)
    lines.append(f"Текущая клетка: «{cell_name}».")
    if dialogue:
        speakers = {"guide": "Ты", "player": "Игрок"}
        lines.append("Разговор на этой клетке до сих пор:")
        lines.extend(f"{speakers.get(role, role)}: «{_clip(text, 700)}»" for role, text in dialogue)
    lines.append(f"Ответ игрока на твоё последнее сообщение: «{player_text}»")
    lines.append(
        "Углуби диалог по этому ответу: не морализируй, не пересказывай сказанное игроком, "
        "заверши либо одним новым вопросом, либо мягким переходом к следующему броску. "
        "Не больше 90 слов."
    )
    return "\n".join(lines)


def build_summary_context(
    intention: str | None,
    visited: list[str],
    gender: str | None = None,
    player_answers: list[str] | None = None,
    resonated: list[str] | None = None,
    not_resonated: list[str] | None = None,
) -> str:
    """`visited` entries already carry snakes and arrows («17 Зависть —змея→ 4 Жадность»);
    `player_answers` — what the player wrote during the game, oldest first."""
    lines = []
    if intention:
        lines.append(f"Запрос игрока на партию: «{intention}».")
    gender_line = _gender_instruction(gender)
    if gender_line:
        lines.append(gender_line)
    lines.append("Пройденный путь по клеткам от начала до конца: " + " → ".join(visited) + ".")
    if player_answers:
        lines.append("Что игрок писал по ходу партии:")
        lines.extend(f"— «{_clip(answer, 300)}»" for answer in player_answers)
    if resonated:
        lines.append("Клетки, которые игрок отметил как откликнувшиеся: " + ", ".join(resonated) + ".")
    if not_resonated:
        lines.append("Клетки, которые игроку не откликнулись: " + ", ".join(not_resonated) + ".")
    lines.append(
        "Дай связное резюме всей партии: не перечисляй клетки построчно, а собери из пути "
        "цельную историю в контексте запроса игрока. Опирайся на его собственные слова и на "
        "повороты пути — змеи и стрелы, — но не цитируй ответы дословно и не пересказывай их "
        "подряд. Заверши одним вопросом, приглашающим взять что-то из этой партии в обычную "
        "жизнь. Не больше 250 слов."
    )
    return "\n".join(lines)
