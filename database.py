import sqlite3
import os
from datetime import datetime

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS summaries (
    story_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT,
    points INTEGER,
    comment_count INTEGER,
    summary TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS last_update (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_updated TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_created_at ON summaries(created_at);
"""

def get_db_path():
    """Get the path to the database file."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'summaries.db')

def init_db():
    """Initialize the database."""
    db_path = get_db_path()
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        # Insert initial last_update record if it doesn't exist
        conn.execute("""
            INSERT OR IGNORE INTO last_update (id, last_updated) 
            VALUES (1, datetime('now', '-24 hours'))
        """)
