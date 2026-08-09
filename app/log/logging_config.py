"""
Logging configuration for the API
"""

import json
import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import override
from zoneinfo import ZoneInfo

import logging_loki

from app.config import get_settings


class TimeZoneFormatter(logging.Formatter):
    """Standard formatter whose timestamps use an explicit IANA time zone."""

    def __init__(self, *args, time_zone: ZoneInfo, **kwargs):
        super().__init__(*args, **kwargs)
        self.time_zone = time_zone

    def formatTime(self, record, datefmt=None):  # noqa: N802
        """Render record timestamps in the configured IANA time zone."""
        timestamp = datetime.fromtimestamp(record.created, self.time_zone)
        return timestamp.strftime(datefmt) if datefmt else timestamp.isoformat()


class CustomFormatter(TimeZoneFormatter):
    """
    Creates custom formatter to color log messages.
    """

    green = '\x1b[32;20m'
    blue = '\x1b[34;20m'
    grey = '\x1b[38;20m'
    yellow = '\x1b[33;20m'
    red = '\x1b[31;20m'
    bold_red = '\x1b[31;1m'
    reset = '\x1b[0m'
    line_level = '%(levelname)s'
    line_format = ': [%(name)s] %(asctime)s => %(message)s (%(filename)s:%(lineno)d)'

    FORMATS = {
        logging.DEBUG: blue + line_level + reset + line_format,
        logging.INFO: green + line_level + reset + line_format,
        logging.WARNING: yellow + line_level + reset + line_format,
        logging.ERROR: red + line_level + reset + line_format,
        logging.CRITICAL: bold_red + line_level + reset + line_format,
    }

    @override
    def format(self, record):
        """
        Provides formatting for log messages.
        """
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = TimeZoneFormatter(
            log_fmt,
            datefmt='%Y-%m-%d %H:%M:%S',
            time_zone=self.time_zone,
        )
        return formatter.format(record)


class GcpJsonFormatter(logging.Formatter):
    """
    One JSON object per line, in the shape Google Cloud Logging ingests
    natively from Cloud Run stdout: ``severity`` maps onto log levels and
    exception tracebacks ride inside ``message`` so Error Reporting can
    group them.
    """

    @override
    def format(self, record):
        """
        Serialize the record as a single-line JSON log entry.
        """
        message = record.getMessage()
        if record.exc_info:
            message = f'{message}\n{self.formatException(record.exc_info)}'
        entry = {
            'severity': record.levelname,
            'message': message,
            'logger': record.name,
            'source': f'{record.filename}:{record.lineno}',
            'env': get_settings().env,
        }
        return json.dumps(entry)


def running_on_cloud_run() -> bool:
    """
    True when executing on Google Cloud Run (which always sets K_SERVICE).
    """
    return bool(os.getenv('K_SERVICE'))


# Create or retrieve a logger
logger = logging.getLogger('aleonard_api')


def configure_logger():
    """
    Configure logger handlers. This function must be called after the .env file is loaded.
    """
    # Avoid adding handlers multiple times
    if logger.hasHandlers():
        return

    settings = get_settings()

    # Create a console handler and set the level
    ch = logging.StreamHandler()

    # Set the level for the logger
    logger.setLevel(settings.log_level.upper())

    # Create a formatter and set it for the handler
    file_log_formatter = TimeZoneFormatter(
        '%(levelname)s: [%(name)s] %(asctime)s => %(message)s '
        '(%(filename)s:%(lineno)d)',
        datefmt='%Y-%m-%d %H:%M:%S',
        time_zone=settings.time_zone_info,
    )

    # Used separate formatters as color ASCII threw off the file log formatting
    # On Cloud Run, stdout is the log pipeline: emit structured JSON instead.
    if running_on_cloud_run():
        ch.setFormatter(GcpJsonFormatter())
    else:
        ch.setFormatter(CustomFormatter(time_zone=settings.time_zone_info))
    logger.addHandler(ch)

    logger.debug('API env set to: %s', settings.env)

    # File + Loki handlers are noise in CI, and on Cloud Run the container
    # filesystem is ephemeral and Cloud Logging already has everything.
    if settings.is_ci or running_on_cloud_run():
        return

    # Mode 'a' appends to the log file across restarts.
    log_file = 'app/log/api.log'
    fh = logging.FileHandler(log_file, mode='a')
    fh.setFormatter(file_log_formatter)

    # Rotating file handler
    rotating_handler = RotatingFileHandler(log_file, maxBytes=10485760, backupCount=10)
    rotating_handler.setFormatter(file_log_formatter)
    logger.addHandler(rotating_handler)
    logger.addHandler(fh)

    # Ship logs to Grafana Loki when a URL is configured.
    if settings.loki_url:
        logging_loki.emitter.LokiEmitter.level_tag = 'level'
        loki_handler = logging_loki.LokiHandler(
            url=f'{settings.loki_url}/loki/api/v1/push',
            version='1',
            tags={
                'service': 'aleonard-us-api',
                'environment': settings.env,
                'landing_zone': settings.lz,
            },
        )
        logger.addHandler(loki_handler)
