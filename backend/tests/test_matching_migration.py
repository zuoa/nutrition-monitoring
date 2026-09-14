"""The matching migration also supports installations bootstrapped by create_all."""
import importlib.util
from pathlib import Path
import sys

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_matching_migration_upgrade_downgrade_and_legacy_bootstrap():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/20260914_0026_add_matching_runs.py"
    spec = importlib.util.spec_from_file_location("matching_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE match_results (id INTEGER PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            columns = {column["name"] for column in sa.inspect(connection).get_columns("match_results")}
            assert {"applied_time_offset_seconds", "match_round", "match_window_seconds"} <= columns
            assert sa.inspect(connection).has_table("matching_runs")
            assert sa.inspect(connection).has_table("matching_candidates")
            migration.downgrade()
            assert not sa.inspect(connection).has_table("matching_runs")
            assert {column["name"] for column in sa.inspect(connection).get_columns("match_results")} == {"id"}
            migration.upgrade()
