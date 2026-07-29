from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import RLock
from typing import Any


MAX_LOG_MESSAGE_LENGTH = 20_000


class RuntimeLogStore(logging.Handler):
    def __init__(self, log_path: Path, max_entries: int = 1_000):
        super().__init__()
        self.log_path = log_path
        self.max_entries = max(100, max_entries)
        self.entries: deque[dict[str, Any]] = deque(maxlen=self.max_entries)
        self.entry_lock = RLock()
        self.next_id = 1
        self.logger: logging.Logger | None = None
        self.file_handler: RotatingFileHandler | None = None

    def install(self, level: str = "INFO") -> None:
        if self.logger is not None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("catgirl")
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        self.setLevel(logger.level)
        self.setFormatter(logging.Formatter("%(message)s"))
        file_handler = RotatingFileHandler(
            self.log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logger.level)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(self)
        logger.addHandler(file_handler)
        self.logger = logger
        self.file_handler = file_handler

    def uninstall(self) -> None:
        if self.logger is None:
            return
        self.logger.removeHandler(self)
        if self.file_handler is not None:
            self.logger.removeHandler(self.file_handler)
            self.file_handler.close()
        self.file_handler = None
        self.logger = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            if record.exc_info:
                formatter = logging.Formatter()
                message = f"{message}\n{formatter.formatException(record.exc_info)}"
            entry = {
                "id": 0,
                "created_at": datetime.fromtimestamp(record.created, timezone.utc),
                "level": record.levelname,
                "source": record.name,
                "message": message[:MAX_LOG_MESSAGE_LENGTH],
            }
            with self.entry_lock:
                entry["id"] = self.next_id
                self.next_id += 1
                self.entries.append(entry)
        except Exception:
            self.handleError(record)

    def read(self, *, after_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        normalized_limit = max(1, min(int(limit), 500))
        with self.entry_lock:
            values = list(self.entries)
        if after_id > 0:
            return [entry.copy() for entry in values if entry["id"] > after_id][
                :normalized_limit
            ]
        return [entry.copy() for entry in values[-normalized_limit:]]

    def clear(self) -> None:
        with self.entry_lock:
            self.entries.clear()
