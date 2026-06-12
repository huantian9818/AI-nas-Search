import logging

from nas_index.logging import CredentialRedactionFilter


def test_redaction_filter_masks_password_and_sid():
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "request password=%s sid=%s",
        ("secret", "abc123"),
        None,
    )
    CredentialRedactionFilter().filter(record)

    assert (
        record.getMessage()
        == "request password=*** sid=***"
    )
