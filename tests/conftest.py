import asyncio

import pytest

from bot.db import Storage

_opened: list[Storage] = []
_original_open = Storage.open


async def _tracking_open(self):
    _opened.append(self)
    await _original_open(self)


@pytest.fixture(autouse=True)
def close_databases(monkeypatch):
    # A failing assertion would otherwise leave aiosqlite's worker thread running,
    # and the test run would hang instead of reporting the failure.
    monkeypatch.setattr(Storage, "open", _tracking_open)
    yield
    while _opened:
        storage = _opened.pop()
        if storage.conn is not None:
            try:
                asyncio.run(storage.conn.close())
            except Exception:
                pass
