# -*- coding: utf-8 -*-
"""Фильтрация спама и определение «особых» новостей."""
import re
from config import (NEWS_MARKERS, NEWS_HASHTAG_RE, SPAM_KEYWORDS,
                    SPECIAL_KEYWORDS, CHAIRMEN_SURNAMES)

_hashtag_re = re.compile(NEWS_HASHTAG_RE, re.IGNORECASE)

# Фамилии: по началу слова, РЕГИСТРОЗАВИСИМО (с заглавной).
# \bНасибулин ловит «Насибулин», «Насибулина», «Насибулину»,
# но не путает фамилию «Депутатов» с обычным словом «депутатов».
_surnames_re = (
    re.compile(r"\b(" + "|".join(re.escape(s) for s in CHAIRMEN_SURNAMES if s) + r")")
    if any(CHAIRMEN_SURNAMES) else None
)


def is_news(text: str) -> bool:
    """True, если это настоящая новость (а не спам/реклама)."""
    if not text or not text.strip():
        return False
    low = text.lower()
    if any(k in low for k in SPAM_KEYWORDS):        # явный спам
        return False
    if any(m in text for m in NEWS_MARKERS):        # маркер ⏺️
        return True
    if _hashtag_re.search(text):                    # хэштег епархии/благочиния
        return True
    return False


def special_reason(text: str):
    """Причина для отдельной публикации либо None."""
    low = text.lower()
    for kw in SPECIAL_KEYWORDS:                      # темы — без учёта регистра
        if kw.lower() in low:
            return f"тема: {kw}"
    if _surnames_re:                                 # фамилии — с заглавной, по началу слова
        m = _surnames_re.search(text)
        if m:
            return f"председатель: {m.group(1)}"
    return None
