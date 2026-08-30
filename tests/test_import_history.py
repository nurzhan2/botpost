# -*- coding: utf-8 -*-
"""Импорт истории из JSON-экспорта Telegram Desktop."""
import json

from conftest import REC_EMOJI, REC_PLAIN
import import_history as ih


# ------------------------------------------------------------ flatten_text

def test_текст_строкой():
    assert ih.flatten_text("простой текст") == "простой текст"


def test_текст_списком_кусков():
    """Telegram Desktop разбивает текст со ссылками/форматированием на куски."""
    value = ["Лекция, подробности ", {"type": "link", "text": "https://t.me/x"},
             {"type": "bold", "text": " — жирным"}]
    assert ih.flatten_text(value) == "Лекция, подробности https://t.me/x — жирным"


def test_текст_отсутствует():
    assert ih.flatten_text(None) == ""
    assert ih.flatten_text([]) == ""


# -------------------------------------------------------------- text_hash

def test_хеш_игнорирует_лишние_пробелы():
    assert ih.text_hash("Крестный ход в Коломне") == ih.text_hash("Крестный  ход\n\nв   Коломне")


def test_разные_тексты_разный_хеш():
    assert ih.text_hash("Первая новость") != ih.text_hash("Вторая новость")


# ------------------------------------------------------------ полный цикл

def _export(tmp_path):
    messages = [
        {"id": 1, "type": "service", "date": "2026-04-10T10:00:00", "action": "create_channel"},
        {"id": 2, "type": "message", "date": "2026-03-01T10:00:00",
         "text": REC_PLAIN + " Слишком старая новость"},
        {"id": 3, "type": "message", "date": "2026-04-16T10:00:00",
         "text": REC_PLAIN + " Крестный ход в Коломне"},
        {"id": 4, "type": "message", "date": "2026-04-17T10:00:00",
         "text": [REC_EMOJI + " Лекция, подробности ", {"type": "link", "text": "https://t.me/x"}]},
        {"id": 5, "type": "message", "date": "2026-04-18T10:00:00", "text": "",
         "photo": "photos/1.jpg"},
        {"id": 6, "type": "message", "date": "2026-04-19T10:00:00",
         "text": "Вакансия: пономарь, зарплата от 40000"},
        {"id": 7, "type": "message", "date": "2026-04-20T10:00:00",
         "text": "Поздравление без маркера"},
        {"id": 8, "type": "message", "date": "2026-04-21T10:00:00",
         "text": REC_PLAIN + " Насибулин возглавил встречу с семьями"},
        {"id": 9, "type": "message", "date": "2026-04-22T10:00:00",
         "text": REC_PLAIN + "   Крестный ход  в Коломне"},      # дубль #3 по пробелам
    ]
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"name": "тест", "messages": messages}, ensure_ascii=False),
                    encoding="utf-8")
    return str(path)


def test_импорт_фильтрует_и_раскладывает_по_видам(fresh_db, tmp_path, capsys):
    export = _export(tmp_path)
    assert ih.main([export, "--since", "2026-04-15"]) == 0

    # 3 обычные новости (#3, #4, #10-хэштега нет) + 1 special (#8)
    assert fresh_db.count_pending("special") == 1
    assert fresh_db.count_pending("digest") == 2

    out = capsys.readouterr().out
    assert "председатель: Насибулин" in out


def test_повторный_прогон_ничего_не_задваивает(fresh_db, tmp_path):
    export = _export(tmp_path)
    ih.main([export, "--since", "2026-04-15"])
    before = (fresh_db.count_pending("digest"), fresh_db.count_pending("special"))

    ih.main([export, "--since", "2026-04-15"])
    after = (fresh_db.count_pending("digest"), fresh_db.count_pending("special"))

    assert before == after


def test_dry_run_ничего_не_пишет(fresh_db, tmp_path):
    export = _export(tmp_path)
    assert ih.main([export, "--since", "2026-04-15", "--dry-run"]) == 0
    assert fresh_db.count_pending("digest") == 0
    assert fresh_db.count_pending("special") == 0


def test_импортированные_идут_без_chat_id_и_msg_id(fresh_db, tmp_path):
    """У истории нет живого оригинала — copy_message невозможен, шлём текстом."""
    ih.main([_export(tmp_path), "--since", "2026-04-15"])
    for _qid, chat_id, msg_id, _text, _reason, _created in fresh_db.pending("digest"):
        assert chat_id is None
        assert msg_id is None
