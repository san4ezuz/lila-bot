"""One logging setup for the api, the bot and the cron scripts: structlog renders every record —
our own `logging.getLogger(__name__)` calls and third-party libraries alike. JSON lines in
production (Railway parses them and indexes `level`), readable lines locally."""

import logging

import structlog

from src.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    renderer: structlog.types.Processor
    if settings.is_production:
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
        shared.append(structlog.processors.format_exc_info)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())
    # Every outgoing request at INFO would drown the game's own lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)
