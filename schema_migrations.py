from sqlalchemy import BigInteger, inspect, text
from extensions import db


def migrate_legacy_columns(engine=None):
    """Add nullable account metadata columns to pre-existing user tables."""
    engine = engine or db.engine
    inspector = inspect(engine)
    if not inspector.has_table('users'):
        return

    existing = {column['name'] for column in inspector.get_columns('users')}
    date_type = 'TIMESTAMP' if engine.dialect.name == 'postgresql' else 'DATETIME'
    additions = {'email_verified_at': date_type, 'consent_at': date_type}
    with engine.begin() as connection:
        for column, sql_type in additions.items():
            if column not in existing:
                connection.execute(text(f'ALTER TABLE users ADD COLUMN {column} {sql_type}'))

        if engine.dialect.name == 'postgresql' and inspector.has_table('documents'):
            document_columns = {
                column['name']: column
                for column in inspector.get_columns('documents')
            }
            file_size_column = document_columns.get('file_size')
            if file_size_column and not isinstance(file_size_column['type'], BigInteger):
                connection.execute(
                    text('ALTER TABLE documents ALTER COLUMN file_size TYPE BIGINT')
                )
