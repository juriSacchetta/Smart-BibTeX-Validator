"""Logging configuration utility"""

import logging
import sys


def setup_logging(debug: bool = False) -> None:
    """Set up logging with optional debug verbosity"""
    level = logging.DEBUG if debug else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    # Silence extremely noisy third-party libs unless debugging
    if not debug:
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("scholarly").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
    else:
        # Keep our package verbose in debug, but tame third-party debug/info noise
        logging.getLogger("bib_validator").setLevel(logging.DEBUG)
        logging.getLogger("scholarly").setLevel(logging.WARNING)
        logging.getLogger("semanticscholar").setLevel(logging.INFO)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
