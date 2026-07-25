# -*- coding: utf-8 -*-
"""Сборка дайджеста с учётом лимита Telegram (4096 символов на сообщение)."""
from config import DIGEST_HEADER

TG_LIMIT = 4096


def normalize(item: str) -> str:
    item = item.strip()
    if not item.startswith("⏺️"):
        item = "⏺️" + item
    return item


def build_digest_messages(items):
    items = [normalize(i) for i in items if i and i.strip()]
    messages = []
    current = DIGEST_HEADER
    for item in items:
        block = "\n\n" + item
        if len(current) + len(block) > TG_LIMIT:
            messages.append(current)
            current = item
        else:
            current += block
    if current:
        messages.append(current)
    return messages
