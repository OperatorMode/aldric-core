import os

import pytest

from storage import db
from storage import event_log


@pytest.fixture(autouse=True)
def reset_storage():
    """Every test gets a clean SQLite DB and empty event log. These modules
    bind their default path at import time, so we reset the same files
    rather than trying to monkeypatch the (already-bound) default arguments.
    """
    for path in (db.DEFAULT_DB_PATH, event_log.DEFAULT_LOG_PATH):
        if os.path.exists(path):
            os.remove(path)
    db.init_db()
    yield
    for path in (db.DEFAULT_DB_PATH, event_log.DEFAULT_LOG_PATH):
        if os.path.exists(path):
            os.remove(path)
