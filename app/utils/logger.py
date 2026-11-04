"""Structured rotating logger."""
import logging, os
from logging.handlers import RotatingFileHandler
from datetime import datetime

os.makedirs("logs", exist_ok=True)
_fmt = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

def _build(name="app") -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG)
    fh = RotatingFileHandler(f"logs/{datetime.now():%Y-%m-%d}.log", maxBytes=5*1024*1024, backupCount=3)
    fh.setFormatter(_fmt); fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setFormatter(_fmt); ch.setLevel(logging.INFO)
    log.addHandler(fh); log.addHandler(ch)
    return log

logger = _build("app")
