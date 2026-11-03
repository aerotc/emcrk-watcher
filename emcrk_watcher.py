#!/usr/bin/env python3
"""
Мониторинг новых закупов авиауслуг на emcrk.kz -> уведомление в Telegram.

Переменные окружения:
  TG_TOKEN      токен бота от @BotFather
  TG_CHAT_ID    ваш chat_id (узнать у @userinfobot или через getUpdates)
  STATE_FILE    (необязательно) путь к файлу состояния, по умолчанию seen.json

Установка:  pip install requests beautifulsoup4
Запуск:     python emcrk_watcher.py   (по cron каждые 10-15 минут)
Проверка:   python emcrk_watcher.py --list   (только показать найденные объявления)
"""
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://emcrk.kz"
ROOT = BASE + "/ru/zakupki/aviatsionnye-i-meditsinskie-uslugi/aviatsionnye-uslugi/"
PAGES = {
    "Международные вылеты": ROOT + "mezhdunarodnye-vylety",
}

STATE_FILE = Path(os.environ.get("STATE_FILE", "seen.json"))
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; tender-watcher/1.0)"}


def tg_send(text: str) -> None:
    if not (TG_TOKEN and TG_CHAT_ID):
        print("[TG не настроен]", text)
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data={"chat_id": TG_CHAT_ID, "text": text, "disable_web_page_preview": "true"},
        timeout=30,
    )
    r.raise_for_status()


def normalize(href: str) -> str:
    # На сайте встречаются битые ссылки вида "https:/images/zakup/x.pdf"
    href = re.sub(r"^https?:/(?!/)", BASE + "/", href.strip())
    href = href.replace("%20images", "images")
    return urljoin(BASE + "/", href)


def fetch_announcements(url: str) -> dict:
    """Возвращает {нормализованная_ссылка: заголовок}."""
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    found = {}
    for a in soup.find_all("a", href=True):
        title = " ".join(a.get_text(" ", strip=True).split())
        href = a["href"]
        is_notice = title.lower().startswith("объявлен")
        is_zakup_file = "/images/zakup/" in href and href.lower().endswith(".pdf")
        if not (is_notice or is_zakup_file):
            continue
        link = normalize(href)
        found.setdefault(link, title or link.rsplit("/", 1)[-1])
    return found


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def list_mode() -> int:
    """Тест парсинга: печатает найденные объявления, ничего не отправляет и не сохраняет."""
    for name, url in PAGES.items():
        found = fetch_announcements(url)
        print(f"{name}: найдено {len(found)}")
        for link, title in found.items():
            print(f"  - {title}\n    {link}")
    return 0


def main() -> int:
    if "--list" in sys.argv:
        return list_mode()
    state = load_state()
    errors = []

    for name, url in PAGES.items():
        try:
            current = fetch_announcements(url)
        except Exception as e:  # сеть, блокировка, смена верстки
            errors.append(f"{name}: {e}")
            continue

        if not current:
            errors.append(f"{name}: на странице не найдено ни одного объявления (возможно, изменилась верстка)")
            continue

        first_run = name not in state
        seen = set(state.get(name, []))
        new = [(l, t) for l, t in current.items() if l not in seen]

        if first_run:
            tg_send(f"Бот запущен. Раздел «{name}»: отслеживаю {len(current)} текущих объявлений.")
        else:
            for link, title in new:
                tg_send(f"🆕 Новый закуп ({name})\n{title}\n{link}")

        state[name] = sorted(seen | set(current))
        save_state(state)

    if errors:
        print("\n".join(errors), file=sys.stderr)
        # Не чаще одного предупреждения в 6 часов, чтобы не спамить при сбое сайта
        if time.time() - state.get("_last_error_alert", 0) > 6 * 3600:
            tg_send("⚠️ Проблема с мониторингом emcrk.kz:\n" + "\n".join(errors))
            state["_last_error_alert"] = time.time()
            save_state(state)
    elif state.get("_last_error_alert"):
        state["_last_error_alert"] = 0
        save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
