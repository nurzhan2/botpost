# -*- coding: utf-8 -*-
"""
Тесты фильтров. Кейсы взяты из scripts/diag_filters.py — те самые, на которых
этап 1 показал, что фильтр режет реальные новости.
"""
import unicodedata

import pytest

from conftest import REC_EMOJI, REC_PLAIN
from filters import (is_news, special_reason, spam_hit, ensure_marker,
                     strip_vs, has_news_marker)


# --------------------------------------------------------------- маркеры

def test_два_написания_кружка_это_разные_строки():
    """Исходная причина поломки: U+23FA и U+23FA U+FE0F не равны."""
    assert REC_PLAIN != REC_EMOJI
    assert len(REC_PLAIN) == 1 and len(REC_EMOJI) == 2


@pytest.mark.parametrize("form", ["NFC", "NFD", "NFKC", "NFKD"])
def test_юникод_нормализация_не_схлопывает_селектор(form):
    """Ни одна из форм нормализации не решает проблему — нужен strip_vs."""
    assert unicodedata.normalize(form, REC_EMOJI) != unicodedata.normalize(form, REC_PLAIN)


def test_strip_vs_схлопывает_оба_написания():
    assert strip_vs(REC_EMOJI) == strip_vs(REC_PLAIN) == REC_PLAIN


@pytest.mark.parametrize("marker", [REC_PLAIN, REC_EMOJI, "🔴", "⚪", "🔵"])
def test_маркер_распознаётся_в_любом_написании(marker):
    assert has_news_marker(marker + " Новость прихода")
    assert is_news(marker + " Новость прихода")


def test_голый_кружок_проходит_фильтр():
    """Регрессия: раньше в NEWS_MARKERS лежал только вариант с U+FE0F."""
    assert is_news(REC_PLAIN + " В Коломне прошёл семейный праздник")


# --------------------------------------------------------------- хэштеги

@pytest.mark.parametrize("text", [
    "Молебен совершён.\n#Коломенская_епархия",
    "Прошла лекция.\n#Одинцовское_благочиние",
    "Семейный праздник.\n#коломенская_епархия",     # нижний регистр
])
def test_хэштег_епархии_проходит_без_маркера(text):
    assert is_news(text)


# ------------------------------------------------------------------ спам

@pytest.mark.parametrize("text", [
    "Требуется помощник в лавку. Зарплата от 40000. Пиши в лс.",
    "Вакансия: пономарь",
    "Требуются сотрудники, график 2/2, опыт работы обязателен",
    "Приглашаем на работу в трапезную",
    "Заработок от 5000 в день",
    "Лёгкий заработок для мам в декрете",
    "Инвестиции от 10000 в криптовалюту",
])
def test_настоящий_спам_отсеивается(text):
    assert spam_hit(text) is not None
    assert not is_news(text)


@pytest.mark.parametrize("text", [
    "Прихожане помогли многодетной маме устроиться на работу в церковную лавку",
    "В беседе обсудили, как заработок отца влияет на уклад семьи",
    "Священник рассказал молодёжи о рисках вложений в крипте",
    "Требуется молитва о болящих",
    "Ищем людей помочь в трапезной на праздник",
])
def test_обычные_новости_не_ловятся_спам_словарём(text):
    """Регрессия: жадные подстроки 'на работу', 'заработок', 'крипт'."""
    assert spam_hit(text) is None


@pytest.mark.parametrize("text", [
    "Прихожане помогли многодетной маме устроиться на работу в церковную лавку",
    "В беседе обсудили, как заработок отца влияет на уклад семьи",
    "Священник рассказал молодёжи о рисках вложений в крипте",
])
def test_такие_новости_с_маркером_проходят_целиком(text):
    assert is_news(REC_PLAIN + " " + text)
    assert is_news(REC_EMOJI + " " + text)


# ------------------------------------------------------- пустое / альбомы

@pytest.mark.parametrize("text", ["", "   ", "\n", None])
def test_пустой_caption_не_новость(text):
    """Второй-десятый кадр альбома приходит без подписи."""
    assert not is_news(text)


def test_текст_без_маркера_и_хэштега_не_новость():
    assert not is_news("Дорогие братья и сёстры, поздравляем всех с праздником!")


# -------------------------------------------------------- special_reason

def test_фамилия_председателя_даёт_special():
    assert special_reason(REC_PLAIN + " Протоиерей Насибулин возглавил встречу") \
        == "председатель: Насибулин"


def test_фамилия_ловится_в_склонениях():
    for form in ("Насибулин", "Насибулина", "Насибулину", "Насибулиным"):
        assert special_reason("Встреча с %s прошла" % form)


def test_строчное_депутатов_не_путается_с_фамилией():
    """Регистрозависимость: обычное слово «депутатов» не должно давать special."""
    assert special_reason("Собрание депутатов района") is None
    assert special_reason("Священник Депутатов провёл беседу") == "председатель: Депутатов"


def test_спец_тема_без_учёта_регистра():
    assert special_reason("Выставка «твоя жизнь до рождения» открылась") \
        == "тема: Твоя жизнь до рождения"


def test_special_reason_на_пустом_не_падает():
    assert special_reason("") is None
    assert special_reason(None) is None


# ------------------------------------------------------- ensure_marker

def test_маркер_не_дублируется():
    """Регрессия 5.3: было '⏺️⏺ текст' из-за расхождения написаний."""
    assert ensure_marker(REC_PLAIN + " Новость") == REC_PLAIN + " Новость"
    assert ensure_marker(REC_EMOJI + " Новость") == REC_EMOJI + " Новость"


def test_маркер_добавляется_если_его_нет():
    out = ensure_marker("Новость без маркера")
    assert out.startswith(REC_PLAIN)
    assert out.count(REC_PLAIN) == 1


def test_ensure_marker_принимает_любой_из_маркеров():
    assert ensure_marker("🔴 Красный") == "🔴 Красный"


def test_ensure_marker_на_пустом():
    assert ensure_marker("") == ""
    assert ensure_marker(None) == ""
