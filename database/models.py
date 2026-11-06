"""
database/models.py — Data structures and schema definitions for the SQLite database.
"""

SCHEMA_SQL = """
-- Monitored Courses
CREATE TABLE IF NOT EXISTS courses (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    instructor TEXT,
    term TEXT,
    status TEXT,
    url TEXT,
    last_synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Announcements
CREATE TABLE IF NOT EXISTS announcements (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT,
    posted_date TEXT,
    author TEXT,
    url TEXT,
    fingerprint TEXT NOT NULL,
    first_detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(course_id) REFERENCES courses(id)
);

-- Activities / Assignments
CREATE TABLE IF NOT EXISTS activities (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    title TEXT NOT NULL,
    due_date TEXT,
    status TEXT,
    points TEXT,
    url TEXT,
    fingerprint TEXT NOT NULL,
    first_detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(course_id) REFERENCES courses(id)
);

-- Notification History to guarantee zero duplicate Discord messages
CREATE TABLE IF NOT EXISTS notification_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL,       -- 'announcement', 'activity', 'deadline_change'
    item_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
