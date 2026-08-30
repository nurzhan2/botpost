# -*- coding: utf-8 -*-
"""
Диагностика фильтров: прогоняет набор реальных/правдоподобных постов @semyadruj
через is_news() и special_reason() и печатает таблицу.

Запуск:  python scripts/diag_filters.py
"""
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import NEWS_MARKERS, SPAM_KEYWORDS          # noqa: E402
from filters import is_news, special_reason             # noqa: E402

# U+23FA BLACK CIRCLE FOR RECORD, с вариационным селектором и без него
REC_PLAIN = "⏺"           # ⏺
REC_EMOJI = "⏺️"     # ⏺️  (то, что лежит в NEWS_MARKERS)

SAMPLES = [
    # (метка, текст)
    ("emoji-маркер (U+23FA U+FE0F)",
     REC_EMOJI + " В Коломне прошёл семейный праздник, посвящённый Дню Петра и Февронии."),

    ("plain-маркер (U+23FA, без FE0F)",
     REC_PLAIN + " В Коломне прошёл семейный праздник, посвящённый Дню Петра и Февронии."),

    ("plain-маркер + длинный текст",
     REC_PLAIN + " Состоялась встреча председателя отдела с многодетными семьями благочиния."),

    ("хэштег #Коломенская_епархия",
     "Молебен о даровании чад совершён в Успенском соборе.\n#Коломенская_епархия"),

    ("хэштег #Одинцовское_благочиние",
     "Прошла лекция для будущих родителей.\n#Одинцовское_благочиние"),

    ("хэштег в нижнем регистре",
     "Семейный праздник в приходе.\n#коломенская_епархия"),

    ("plain-маркер + фамилия председателя",
     REC_PLAIN + " Протоиерей Насибулин возглавил встречу с молодыми семьями."),

    ("emoji-маркер + фамилия Депутатов",
     REC_EMOJI + " Священник Депутатов провёл беседу в школе."),

    ("спец-тема «Твоя жизнь до рождения»",
     REC_EMOJI + " Выставка «Твоя жизнь до рождения» открылась в Подольске."),

    ("обычная новость со словами «на работу»",
     REC_PLAIN + " Прихожане помогли многодетной маме устроиться на работу в церковную лавку."),

    ("обычная новость со словом «заработок»",
     REC_EMOJI + " В беседе обсудили, как заработок отца влияет на уклад семьи."),

    ("обычная новость со словом «крипта/крипте»",
     REC_EMOJI + " Священник рассказал молодёжи о рисках вложений в крипте."),

    ("настоящая вакансия (спам, должен отсеяться)",
     "Требуется помощник в лавку. Зарплата от 40000. Пиши в лс."),

    ("пустой caption (2-е фото альбома)",
     ""),

    ("текст без маркера и без хэштега",
     "Дорогие братья и сёстры, поздравляем всех с праздником!"),
]


def cp(s, n=6):
    """Кодпоинты первых n символов."""
    return " ".join("U+%04X" % ord(c) for c in s[:n])


def main():
    print("=" * 100)
    print("NEWS_MARKERS =", [cp(m, 4) for m in NEWS_MARKERS], "->", NEWS_MARKERS)
    print("=" * 100)

    # --- Ключевая проверка: ⏺️ (U+23FA U+FE0F) vs ⏺ (U+23FA) ---
    print("\n### ПРОВЕРКА МАРКЕРА: вариационный селектор U+FE0F ###")
    print("REC_EMOJI  = %-14s len=%d  %s" % (repr(REC_EMOJI), len(REC_EMOJI), cp(REC_EMOJI)))
    print("REC_PLAIN  = %-14s len=%d  %s" % (repr(REC_PLAIN), len(REC_PLAIN), cp(REC_PLAIN)))
    print("REC_EMOJI == REC_PLAIN                 ->", REC_EMOJI == REC_PLAIN)
    print("any(m in REC_EMOJI_text) [текущий код] ->",
          any(m in (REC_EMOJI + " новость") for m in NEWS_MARKERS))
    print("any(m in REC_PLAIN_text) [текущий код] ->",
          any(m in (REC_PLAIN + " новость") for m in NEWS_MARKERS))
    for form in ("NFC", "NFD", "NFKC", "NFKD"):
        print("  unicodedata.normalize(%-5s): emoji==plain -> %s" % (
            form,
            unicodedata.normalize(form, REC_EMOJI) == unicodedata.normalize(form, REC_PLAIN)))
    print("  после удаления U+FE0F: emoji==plain ->",
          REC_EMOJI.replace("️", "") == REC_PLAIN.replace("️", ""))

    # --- Таблица ---
    print("\n### ПРОГОН ОБРАЗЦОВ ###")
    hdr = "%-3s | %-38s | %-8s | %-24s | %s" % ("#", "случай", "is_news", "special_reason", "текст (80)")
    print(hdr)
    print("-" * len(hdr))
    passed = 0
    for i, (label, text) in enumerate(SAMPLES, 1):
        ok = is_news(text)
        reason = special_reason(text) if text else None
        if ok:
            passed += 1
        preview = (text[:80].replace("\n", "\\n")) or "<пусто>"
        print("%-3d | %-38s | %-8s | %-24s | %s" % (
            i, label, "PASS" if ok else "skip", reason or "-", preview))

    print("-" * len(hdr))
    print("Прошло фильтр: %d из %d" % (passed, len(SAMPLES)))

    # --- Какие спам-слова кого убили ---
    print("\n### ПОЧЕМУ ОТСЕЯЛОСЬ (спам-словарь) ###")
    for i, (label, text) in enumerate(SAMPLES, 1):
        if not text:
            continue
        low = text.lower()
        hits = [k for k in SPAM_KEYWORDS if k in low]
        if hits:
            print("%-3d %-38s убит словами: %s" % (i, label, hits))


if __name__ == "__main__":
    main()
