import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


class CredentialRedactionFilter(logging.Filter):
    _pattern = re.compile(
        r"(?i)(password|pwd|sid|authSid|qtoken)=([^&\s]+)"
    )

    def filter(
        self,
        record: logging.LogRecord,
    ) -> bool:
        message = record.getMessage()
        redacted = self._pattern.sub(
            lambda match: (
                f"{match.group(1)}=***"
            ),
            message,
        )
        record.msg = redacted
        record.args = ()
        return True


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    handler = RotatingFileHandler(
        log_dir / "nas-index.log",
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.addFilter(
        CredentialRedactionFilter()
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s "
            "%(name)s %(message)s"
        )
    )
    logger = logging.getLogger("nas_index")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(handler)
    logging.getLogger("httpx").setLevel(
        logging.WARNING
    )
    logging.getLogger("httpcore").setLevel(
        logging.WARNING
    )
