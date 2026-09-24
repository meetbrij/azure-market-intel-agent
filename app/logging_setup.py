"""One logging setup for the CLI, API and worker."""

import logging

NOISY_LOGGERS = ("azure", "httpx", "httpx2", "openai")


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(message)s")
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
