"""Logging configuration for the AI Scientist system."""

import logging
import sys
from typing import Optional


def setup_logging(
    level: int = logging.INFO,
    format_string: Optional[str] = None,
) -> logging.Logger:
    """Configure and return the scientist logger.

    Args:
        level: Logging level (default: INFO)
        format_string: Custom format string (optional)

    Returns:
        Configured logger instance
    """
    if format_string is None:
        format_string = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

    # Create formatter
    formatter = logging.Formatter(format_string, datefmt="%H:%M:%S")

    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # Get or create the scientist logger
    logger = logging.getLogger("scientist")
    logger.setLevel(level)

    # Avoid adding duplicate handlers
    if not logger.handlers:
        logger.addHandler(console_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger


def get_logger(name: str = "scientist") -> logging.Logger:
    """Get a logger instance for a specific module.

    Args:
        name: Logger name (will be prefixed with 'scientist.')

    Returns:
        Logger instance
    """
    if name == "scientist":
        return logging.getLogger("scientist")
    return logging.getLogger(f"scientist.{name}")


# Pre-configured log level shortcuts
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR

