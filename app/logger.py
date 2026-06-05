import logging
from logging.handlers import RotatingFileHandler

from .config import ensure_runtime_dirs, get_settings


def setup_logging() -> logging.Logger:
    ensure_runtime_dirs()
    settings = get_settings()
    logger = logging.getLogger("crawler")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    log_file = settings.path(settings.logs_dir) / "crawler.log"
    file_handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


log = setup_logging()
