from __future__ import annotations

import io
import logging
import threading

import smart.logging as smart_logging


def _reset_smart_logger() -> logging.Logger:
    logger = logging.getLogger("smart")
    logger.handlers[:] = []
    logger.propagate = True
    smart_logging._CONFIGURED = False
    return logger


def test_smart_logger_does_not_duplicate_messages_through_root() -> None:
    root_logger = logging.getLogger()
    original_root_handlers = list(root_logger.handlers)
    original_root_level = root_logger.level
    smart_logger = logging.getLogger("smart")
    original_smart_handlers = list(smart_logger.handlers)
    original_smart_level = smart_logger.level
    original_smart_propagate = smart_logger.propagate
    original_configured = smart_logging._CONFIGURED

    root_stream = io.StringIO()
    root_logger.handlers[:] = [logging.StreamHandler(root_stream)]
    root_logger.setLevel(logging.WARNING)

    try:
        smart_logger = _reset_smart_logger()
        logger = smart_logging.get_logger("smart.test")
        smart_stream = io.StringIO()
        for handler in smart_logger.handlers:
            handler.stream = smart_stream

        logger.warning("duplicate-check")

        assert smart_logger.propagate is False
        assert smart_stream.getvalue().count("duplicate-check") == 1
        assert "duplicate-check" not in root_stream.getvalue()
    finally:
        root_logger.handlers[:] = original_root_handlers
        root_logger.setLevel(original_root_level)
        smart_logger.handlers[:] = original_smart_handlers
        smart_logger.setLevel(original_smart_level)
        smart_logger.propagate = original_smart_propagate
        smart_logging._CONFIGURED = original_configured


def test_smart_logger_configuration_is_thread_safe() -> None:
    smart_logger = logging.getLogger("smart")
    original_smart_handlers = list(smart_logger.handlers)
    original_smart_level = smart_logger.level
    original_smart_propagate = smart_logger.propagate
    original_configured = smart_logging._CONFIGURED

    try:
        smart_logger = _reset_smart_logger()

        def worker() -> None:
            smart_logging.get_logger("smart.concurrent")

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(smart_logger.handlers) == 1
        assert smart_logger.propagate is False
    finally:
        smart_logger.handlers[:] = original_smart_handlers
        smart_logger.setLevel(original_smart_level)
        smart_logger.propagate = original_smart_propagate
        smart_logging._CONFIGURED = original_configured
