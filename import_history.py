# -*- coding: utf-8 -*-
"""
Разовая заливка истории канала-источника из экспорта Telegram Desktop.

Зачем: Bot API не умеет читать историю канала задним числом — бот видит только
посты, появившиеся ПОСЛЕ добавления его в администраторы. Поэтому старьё
(с апреля) заливается отсюда.

Как получить экспорт:
    Telegram Desktop -> открыть канал @semyadruj -> ⋮ -> «Экспорт истории чата»
    -> формат JSON (машиночитаемый), медиа можно не выгружать
    -> получится папка с файлом result.json

Запуск:
    python import_history.py result.json --since 2026-04-15 --dry-run   # посмотреть
    python import_history.py result.json --since 2026-04-15             # залить

Идемпотентность: каждый импортированный текст запоминается по sha256 в таблице
`imported`, поэтому повторный прогон того же файла ничего не задваивает.
Посты кладутся в очередь с chat_id=NULL и msg_id=NULL — у них нет живого
оригинала в канале, копировать нечего, они опубликуются текстом.
"""
import argparse
import datetime
import hashlib
import json
import sys

import config
import db
from filters import is_news, special_reason, spam_hit


def flatten_text(value) -> str:
    """Telegram Desktop кладёт в 'text' либо строку, либо список кусков.

    Список выглядит так: ["обычный текст", {"type":"link","text":"https://..."},
    {"type":"bold","text":"жирное"}]. Склеиваем в плоскую строку.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for chunk in value:
            if isinstance(chunk, str):
                parts.append(chunk)
            elif isinstance(chunk, dict):
                parts.append(chunk.get("text", ""))
        return "".join(parts)
    return ""


def text_hash(text: str) -> str:
    """Хеш по схлопнутым пробелам — мелкая переверстка не создаёт дубль."""
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_date(msg):
    raw = msg.get("date")
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None


def load_messages(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        name = data.get("name")
        msgs = data.get("messages", [])
    else:                       # на случай, если подсунули голый список
        name, msgs = None, data
    return name, msgs


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Импорт истории канала из JSON-экспорта Telegram Desktop")
    ap.add_argument("export", help="путь к result.json из экспорта Telegram Desktop")
    ap.add_argument("--since", default=config.IMPORT_START_DATE,
                    help="брать посты не раньше этой даты, ГГГГ-ММ-ДД (по умолчанию %(default)s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="только показать, что было бы залито, ничего не писать в БД")
    ap.add_argument("--limit", type=int, default=0, help="залить не более N постов (0 = без лимита)")
    args = ap.parse_args(argv)

    try:
        since = datetime.datetime.fromisoformat(args.since)
    except ValueError:
        ap.error("--since должен быть в формате ГГГГ-ММ-ДД, получено %r" % args.since)

    try:
        chan, messages = load_messages(args.export)
    except FileNotFoundError:
        print("Файл не найден: %s" % args.export)
        return 1
    except json.JSONDecodeError as e:
        print("Это не похоже на JSON-экспорт Telegram Desktop: %s" % e)
        return 1

    print("Экспорт: %s" % args.export)
    if chan:
        print("Канал: %s" % chan)
    print("Сообщений в файле: %d, берём начиная с %s" % (len(messages), since.date()))
    if args.dry_run:
        print("РЕЖИМ ПРОСМОТРА — новости не заливаются")
    print("-" * 78)

    # init() нужен и в dry-run: он только создаёт пустые таблицы
    # (CREATE TABLE IF NOT EXISTS), а без них не отработает проверка на дубли.
    db.init()

    stats = {"старые": 0, "служебные": 0, "пусто": 0, "спам": 0,
             "не новость": 0, "дубль": 0, "залито": 0}
    seen_hashes = set()      # чтобы не задваивать внутри одного файла

    for msg in messages:
        if msg.get("type") != "message":
            stats["служебные"] += 1
            continue

        dt = parse_date(msg)
        if dt is not None and dt < since:
            stats["старые"] += 1
            continue

        text = flatten_text(msg.get("text", "")).strip()
        if not text:
            # Обычно это кадр альбома без подписи — подпись лежит у соседнего.
            stats["пусто"] += 1
            continue

        hit = spam_hit(text)
        if hit:
            stats["спам"] += 1
            continue
        if not is_news(text):
            stats["не новость"] += 1
            continue

        h = text_hash(text)
        if h in seen_hashes:
            stats["дубль"] += 1
            continue
        seen_hashes.add(h)

        reason = special_reason(text)
        kind = "special" if reason else "digest"

        if args.dry_run:
            if db.queue_has_text(text):
                stats["дубль"] += 1
                continue
            stats["залито"] += 1
            print("[%s] %s%s" % (kind, text[:88].replace("\n", " "),
                                 (" | %s" % reason) if reason else ""))
        else:
            if db.queue_has_text(text):
                stats["дубль"] += 1
                continue
            if db.import_enqueue(h, text, kind, reason or ""):
                stats["залито"] += 1
                print("[%s] %s%s" % (kind, text[:88].replace("\n", " "),
                                     (" | %s" % reason) if reason else ""))
            else:
                stats["дубль"] += 1

        if args.limit and stats["залито"] >= args.limit:
            print("...достигнут --limit %d, останавливаюсь" % args.limit)
            break

    print("-" * 78)
    for k, v in stats.items():
        print("%-12s %d" % (k + ":", v))
    if args.dry_run:
        print("\nЭто был просмотр. Убери --dry-run, чтобы залить в базу.")
    else:
        print("\nВ очереди сейчас: digest=%d special=%d"
              % (db.count_pending("digest"), db.count_pending("special")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
