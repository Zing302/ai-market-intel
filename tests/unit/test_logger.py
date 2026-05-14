import logging
from pathlib import Path

from utils.logger import get_logger


def test_returns_logger_instance():
    logger = get_logger("test_smoke")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "test_smoke"


def test_caches_handlers_across_calls():
    a = get_logger("test_cache")
    b = get_logger("test_cache")
    assert a is b
    assert len(a.handlers) == len(b.handlers)


def test_writes_to_dated_logfile(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.logger.LOG_DIR", str(tmp_path))
    logger = get_logger("test_file_write")
    logger.info("hello from test")
    for h in logger.handlers:
        h.flush()
    log_files = list(Path(tmp_path).glob("test_file_write_*.log"))
    assert len(log_files) == 1
    assert "hello from test" in log_files[0].read_text()
