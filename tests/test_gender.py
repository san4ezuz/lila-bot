import pytest

from src.llm.gender import infer_gender


@pytest.mark.parametrize(
    "text",
    [
        "я устала всё время подстраиваться",
        "Я так и не решила, куда идти",
        "я сама не знаю",
        "я готова попробовать",
        "Я пришла с вопросом про работу",
        "я давно запуталась",
    ],
)
def test_female(text):
    assert infer_gender(text) == "female"


@pytest.mark.parametrize(
    "text",
    [
        "я устал от этой работы",
        "Я сам не понимаю, чего хочу",
        "я пришёл разобраться с застоем",
        "я не готов менять всё сразу",
        "я опять запутался",
    ],
)
def test_male(text):
    assert infer_gender(text) == "male"


@pytest.mark.parametrize(
    "text",
    [
        "не знаю",
        "отношения с мамой",
        "я хочу сдвинуться с места",
        "я думаю, что он устал",  # someone else's gender
        "мне кажется, она ушла",
        "я один раз хочу попробовать",  # «один» is not a gender marker
        "я устала, а муж говорит, что я сам виноват",  # mixed: no signal
    ],
)
def test_no_signal(text):
    assert infer_gender(text) is None
