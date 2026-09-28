"""Pure Core logic tests do not require a PostgreSQL fixture.

Controller proposal reception (S07f) is pure mapping/decision logic over
shared contracts types and the sealing SourcePrediction; it imports Core
modules but never touches the database.
"""

import pytest


@pytest.fixture(autouse=True)
def clean_tables():
    """Override the repository's database cleanup for these pure tests."""
    yield
