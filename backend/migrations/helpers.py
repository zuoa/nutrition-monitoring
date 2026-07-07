"""Small helpers for migrations that must tolerate legacy create_all schemas."""

from alembic import op
import sqlalchemy as sa


def _inspector():
    return sa.inspect(op.get_bind())


def table_exists(table_name):
    return _inspector().has_table(table_name)


def column_exists(table_name, column_name):
    if not table_exists(table_name):
        return False
    return any(column["name"] == column_name for column in _inspector().get_columns(table_name))


def column_type(table_name, column_name):
    if not table_exists(table_name):
        return None
    for column in _inspector().get_columns(table_name):
        if column["name"] == column_name:
            return column["type"]
    return None


def index_exists(table_name, index_name):
    if not table_exists(table_name):
        return False
    return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def unique_constraint_exists(table_name, constraint_name=None, columns=None):
    if not table_exists(table_name):
        return False

    expected_columns = list(columns or [])
    inspector = _inspector()
    for constraint in inspector.get_unique_constraints(table_name):
        if constraint_name and constraint.get("name") == constraint_name:
            return True
        if expected_columns and list(constraint.get("column_names") or []) == expected_columns:
            return True

    for index in inspector.get_indexes(table_name):
        if index.get("unique") and expected_columns and list(index.get("column_names") or []) == expected_columns:
            return True
    return False


def foreign_key_exists(table_name, constraint_name=None, columns=None, referred_table=None):
    if not table_exists(table_name):
        return False

    expected_columns = list(columns or [])
    for fk in _inspector().get_foreign_keys(table_name):
        if constraint_name and fk.get("name") == constraint_name:
            return True
        if expected_columns and list(fk.get("constrained_columns") or []) != expected_columns:
            continue
        if referred_table and fk.get("referred_table") != referred_table:
            continue
        if expected_columns or referred_table:
            return True
    return False


def add_column_if_not_exists(table_name, column):
    if not column_exists(table_name, column.name):
        op.add_column(table_name, column)


def drop_column_if_exists(table_name, column_name):
    if column_exists(table_name, column_name):
        op.drop_column(table_name, column_name)


def create_index_if_not_exists(index_name, table_name, columns, unique=False):
    if table_exists(table_name) and not index_exists(table_name, index_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def drop_index_if_exists(index_name, table_name):
    if index_exists(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def create_unique_constraint_if_not_exists(constraint_name, table_name, columns):
    if table_exists(table_name) and not unique_constraint_exists(table_name, constraint_name, columns):
        op.create_unique_constraint(constraint_name, table_name, columns)


def drop_constraint_if_exists(constraint_name, table_name, type_=None):
    if not table_exists(table_name):
        return

    exists = False
    if type_ == "foreignkey":
        exists = foreign_key_exists(table_name, constraint_name=constraint_name)
    elif type_ == "unique":
        exists = unique_constraint_exists(table_name, constraint_name=constraint_name)
    else:
        exists = (
            foreign_key_exists(table_name, constraint_name=constraint_name)
            or unique_constraint_exists(table_name, constraint_name=constraint_name)
        )

    if exists:
        op.drop_constraint(constraint_name, table_name, type_=type_)


def create_foreign_key_if_not_exists(constraint_name, source_table, referent_table, local_cols, remote_cols):
    if table_exists(source_table) and not foreign_key_exists(
        source_table,
        constraint_name=constraint_name,
        columns=local_cols,
        referred_table=referent_table,
    ):
        op.create_foreign_key(constraint_name, source_table, referent_table, local_cols, remote_cols)


def drop_table_if_exists(table_name):
    if table_exists(table_name):
        op.drop_table(table_name)
