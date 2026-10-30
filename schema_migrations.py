from sqlalchemy import inspect, text
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
