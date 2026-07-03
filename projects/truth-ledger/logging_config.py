import sys
import warnings
from pathlib import Path

from loguru import logger

from src.config import settings


def setup_logging():
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    logger.remove()

    console_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    logger.add(
        sys.stdout,
        format=console_format,
        level=settings.log_level,
        colorize=True,
        enqueue=True,
        backtrace=True,
    )
    logger.add(
        logs_dir / "aria_{time:YYYY-MM-DD}.log",
        rotation="5 MB",
        retention="7 days",
        level="DEBUG",
        enqueue=True,
        backtrace=True,
    )

    warnings.simplefilter("always")

    def warning_handler(message, category, filename, lineno, *_args):
        logger.warning(f"{category.__name__}: {message} ({filename}:{lineno})")

    warnings.showwarning = warning_handler

    logger.info("Loguru configuré avec succès")
    return logger


log = setup_logging()
