import sqlite3
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

SCHEMA_V1_SQL = """
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

MIGRATION_V2_SQL = """
-- First create a temporary table with the new schema
CREATE TABLE summaries_new (
    story_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT,
    points INTEGER,
    comment_count INTEGER,
    summary TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL
);

-- Copy data from old table to new table
INSERT INTO summaries_new 
    (story_id, title, url, points, comment_count, summary, created_at)
SELECT 
    story_id, title, url, points, comment_count, summary, created_at
FROM summaries;

-- Drop the old table
DROP TABLE summaries;

-- Rename the new table to the original name
ALTER TABLE summaries_new RENAME TO summaries;

-- Recreate the indexes
CREATE INDEX IF NOT EXISTS idx_created_at ON summaries(created_at);
CREATE INDEX IF NOT EXISTS idx_position ON summaries(position);
"""

def get_db_path():
    """Get the path to the database file."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'summaries.db')

def check_if_position_exists(conn):
    """Check if position column exists in summaries table."""
    cursor = conn.execute("PRAGMA table_info(summaries)")
    columns = cursor.fetchall()
    return any(column[1] == 'position' for column in columns)

def init_db():
    """Initialize the database and handle migrations."""
    db_path = get_db_path()
    
    # Check if this is a new database
    is_new_db = not os.path.exists(db_path)
    
    with sqlite3.connect(db_path) as conn:
        if is_new_db:
            logger.info("Creating new database with latest schema")
            # For new database, create with position column
            conn.executescript(SCHEMA_V1_SQL)
            conn.executescript(MIGRATION_V2_SQL)
            # Insert initial last_update record
            conn.execute("""
                INSERT OR IGNORE INTO last_update (id, last_updated) 
                VALUES (1, datetime('now', '-24 hours'))
            """)
        else:
            logger.info("Checking existing database for updates")
            # For existing database, check if we need to add position column
            if not check_if_position_exists(conn):
                logger.info("Migrating database to include position column")
                conn.executescript(MIGRATION_V2_SQL)
                logger.info("Database migration completed")