import importlib.util
from pathlib import Path
import sys

from alembic.migration import MigrationContext
from alembic.operations import Operations
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_resident_slots_migration_preserves_dishes_and_supports_create_all():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/20261012_0028_add_dish_resident_meal_slots.py"
    spec = importlib.util.spec_from_file_location("resident_slots_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        connection.execute(sa.text("CREATE TABLE dishes (id INTEGER PRIMARY KEY, name TEXT)"))
        connection.execute(sa.text("INSERT INTO dishes VALUES (1, '米饭')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            assert connection.execute(sa.text("SELECT COUNT(*) FROM dish_resident_meal_slots")).scalar() == 0
            connection.execute(sa.text("INSERT INTO dish_resident_meal_slots (dish_id, meal_slot) VALUES (1, 'lunch')"))
            connection.execute(sa.text("DELETE FROM dishes WHERE id=1"))
            assert connection.execute(sa.text("SELECT COUNT(*) FROM dish_resident_meal_slots")).scalar() == 0
            migration.downgrade()
            assert not sa.inspect(connection).has_table("dish_resident_meal_slots")
            assert sa.inspect(connection).has_table("dishes")
            migration.upgrade()
