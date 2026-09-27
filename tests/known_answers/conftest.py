"""Pure quantitative known answers do not require a PostgreSQL fixture."""

import pytest


@pytest.fixture(autouse=True)
def clean_tables():
    """Override the repository's database cleanup for these pure tests."""

