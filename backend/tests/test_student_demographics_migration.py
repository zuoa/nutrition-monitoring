import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, inspect


def test_student_demographics_migration_upgrades_existing_rows_and_downgrades():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/20261018_0030_add_student_demographics.py"
    spec = importlib.util.spec_from_file_location("student_demographics_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    students = Table("students", metadata, Column("id", Integer, primary_key=True))
    with engine.begin() as connection:
        metadata.create_all(connection)
        connection.execute(students.insert().values(id=1))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            columns = {column["name"] for column in inspect(connection).get_columns("students")}
            assert {"age", "birth_date"} <= columns
            assert connection.exec_driver_sql("SELECT age, birth_date FROM students WHERE id = 1").one() == (None, None)
            migration.downgrade()
        assert {column["name"] for column in inspect(connection).get_columns("students")} == {"id"}
    engine.dispose()
