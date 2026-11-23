from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def setup_logger(
    name: str = "youtube_collector",
    log_dir: Path | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """標準出力＋日次ローテーションファイル出力のロガーを構築する."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    target_dir = log_dir or (Path(__file__).resolve().parent.parent.parent / "logs")
    target_dir.mkdir(parents=True, exist_ok=True)
    log_file = target_dir / "collector.log"

    file_handler = TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.propagate = False
    return logger
