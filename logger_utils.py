import logging
import os
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def setup_logging():
    """Configure logging with both console and timestamped file handlers."""
    logs_dir = os.path.join(SCRIPT_DIR, 'logs')
    os.makedirs(logs_dir, exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    log_file = os.path.join(
        logs_dir,
        f"telegram_bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
    )
    file_handler = logging.FileHandler(log_file, encoding="utf-8")

    console_handler.setLevel(logging.INFO)
    file_handler.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    logger.handlers = []
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logging.getLogger(__name__)
