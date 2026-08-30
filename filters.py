# -*- coding: utf-8 -*-
"""
Фильтрация спама и определение «особых» новостей.

ЕДИНОЕ МЕСТО работы с маркерами-кружками. И filters, и digest ходят сюда —
раньше маркер был захардкожен в двух местах ("⏺️" в config.NEWS_MARKERS и
"⏺️" в digest.normalize) и они разъехались по вариационному селектору.
"""
import re

from config import (NEWS_MARKERS, DIGEST_MARKER, NEWS_HASHTAG_RE, SPAM_PATTERNS,
                    SPECIAL_KEYWORDS, CHAIRMEN_SURNAMES)

# U+FE0F VARIATION SELECTOR-16 — «показывай предыдущий символ как эмодзи».
# Невидим, но делает строку другой: "⏺" != "⏺️". Unicode-нормализация
# (NFC/NFD/NFKC/NFKD) его НЕ убирает, поэтому режем вручную.
VS16 = "️"
# U+200D ZERO WIDTH JOINER — на всякий случай, встречается в составных эмодзи.
ZWJ = "‍"


def strip_vs(text: str) -> str:
    """Убрать вариационные селекторы, чтобы маркеры сравнивались устойчиво."""
    if not text:
        return ""
    return text.replace(VS16, "").replace(ZWJ, "")


# Маркеры в канонической форме — без селекторов.
_MARKERS = tuple(strip_vs(m) for m in NEWS_MARKERS if m)
_DIGEST_MARKER = strip_vs(DIGEST_MARKER)

_hashtag_re = re.compile(NEWS_HASHTAG_RE, re.IGNORECASE)

# Спам: регулярки с границами слов, а не подстроки.
_spam_res = [(p, re.compile(p, re.IGNORECASE)) for p in SPAM_PATTERNS]

# Фамилии: по началу слова, РЕГИСТРОЗАВИСИМО (с заглавной).
# \bНасибулин ловит «Насибулин», «Насибулина», «Насибулину»,
# но не путает фамилию «Депутатов» с обычным словом «депутатов».
_surnames_re = (
    re.compile(r"\b(" + "|".join(re.escape(s) for s in CHAIRMEN_SURNAMES if s) + r")")
    if any(CHAIRMEN_SURNAMES) else None
)


def has_news_marker(text: str) -> bool:
    """Есть ли в тексте маркер-кружок (в любом написании — с U+FE0F или без)."""
    clean = strip_vs(text)
    return any(m in clean for m in _MARKERS)


def spam_hit(text: str):
    """Вернуть сработавший спам-шаблон либо None. Отдельно от is_news ради логов."""
    if not text:
        return None
    for pattern, rx in _spam_res:
        if rx.search(text):
            return pattern
    return None


def ensure_marker(item: str) -> str:
    """Гарантировать ровно один маркер в начале пункта дайджеста.

    Проверка идёт по нормализованной строке, поэтому пункт, начинающийся с
    голого "⏺", НЕ получит второй маркер (раньше выходило "⏺️⏺ текст").
    """
    item = (item or "").strip()
    if not item:
        return item
    if any(strip_vs(item).startswith(m) for m in _MARKERS):
        return item
    return _DIGEST_MARKER + " " + item


def is_news(text: str) -> bool:
    """True, если это настоящая новость (а не спам/реклама)."""
    if not text or not text.strip():
        return False
    if spam_hit(text):                              # явный спам
        return False
    if has_news_marker(text):                       # маркер-кружок
        return True
    if _hashtag_re.search(text):                    # хэштег епархии/благочиния
        return True
    return False


def special_reason(text: str):
    """Причина для отдельной публикации либо None."""
    if not text:
        return None
    low = text.lower()
    for kw in SPECIAL_KEYWORDS:                      # темы — без учёта регистра
        if kw.lower() in low:
            return f"тема: {kw}"
    if _surnames_re:                                 # фамилии — с заглавной, по началу слова
        m = _surnames_re.search(text)
        if m:
            return f"председатель: {m.group(1)}"
    return None
