import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, inspect


def test_dish_tags_migration_preserves_existing_rows_and_can_roll_back():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/20261019_0031_add_dish_tags.py"
    spec = importlib.util.spec_from_file_location("dish_tags_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    dishes = Table("dishes", metadata, Column("id", Integer, primary_key=True))
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(dishes.insert().values(id=1))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.exec_driver_sql("SELECT food_groups, cooking_method, vegetable_types FROM dishes WHERE id = 1").one() == (None, None, None)
            migration.downgrade()
        assert {column["name"] for column in inspect(connection).get_columns("dishes")} == {"id"}
    engine.dispose()
