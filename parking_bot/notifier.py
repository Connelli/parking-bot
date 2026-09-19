from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id

    def send(self, message: str) -> None:
        try:
            resp = httpx.post(self._url, json={"chat_id": self._chat_id, "text": message}, timeout=10.0)
            resp.raise_for_status()
        except httpx.HTTPError:
            log.exception("failed to send telegram notification: %s", message)
