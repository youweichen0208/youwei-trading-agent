from unittest.mock import patch

import pytest
from yta_alpha.migrate import migrate


def test_invalid_migration_catalog_never_connects(tmp_path):
    with patch("yta_alpha.migrate.psycopg.connect") as connect:
        for path in (tmp_path / "missing", tmp_path):
            with pytest.raises(RuntimeError, match="missing or empty"):
                migrate("private-db-url", path)
        (tmp_path / "001.sql").write_text(" \n")
        with pytest.raises(RuntimeError, match="empty script"):
            migrate("private-db-url", tmp_path)
        connect.assert_not_called()
