"""Pure protocol tests do not need the application's PostgreSQL fixture."""
import pytest


@pytest.fixture(autouse=True)
def clean_tables():
    yield
