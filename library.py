# -*- coding: utf-8 -*-
"""library.py — SQLite 书库"""
import os, sqlite3, threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS books(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT DEFAULT '',
    path TEXT UNIQUE NOT NULL,
    format TEXT DEFAULT '',
    size INTEGER DEFAULT 0,
    cover_file TEXT DEFAULT '',
    added TEXT DEFAULT (datetime('now','localtime')),
    last_chapter INTEGER DEFAULT 0,
    last_scroll REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bookmarks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_path TEXT NOT NULL,
    chapter INTEGER NOT NULL,
    scroll REAL DEFAULT 0,
    label TEXT DEFAULT '',
    created TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_bookmarks_path ON bookmarks(book_path);
CREATE TABLE IF NOT EXISTS reading_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_path TEXT NOT NULL,
    title TEXT DEFAULT '',
    chapter INTEGER DEFAULT 0,
    chapter_title TEXT DEFAULT '',
    scroll REAL DEFAULT 0,
    ts TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_log_path ON reading_log(book_path);
"""

class Library:
    def __init__(self, db_path):
        self.db_path = db_path
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.commit()
        self._migrate()

    def _migrate(self):
        """旧库升级：补 last_chapter / last_scroll 列"""
        with self.lock:
            cols = {r[1] for r in self.conn.execute('PRAGMA table_info(books)')}
            if 'last_chapter' not in cols:
                self.conn.execute('ALTER TABLE books ADD COLUMN last_chapter INTEGER DEFAULT 0')
            if 'last_scroll' not in cols:
                self.conn.execute('ALTER TABLE books ADD COLUMN last_scroll REAL DEFAULT 0')
            self.conn.commit()

    def add(self, probe, path, cover_file=''):
        """加入一本书（按路径去重），返回是否新加"""
        with self.lock:
            cur = self.conn.execute(
                'SELECT id FROM books WHERE path=?', (path,))
            if cur.fetchone():
                return False
            self.conn.execute(
                'INSERT INTO books(title, author, path, format, size, cover_file) '
                'VALUES(?,?,?,?,?,?)',
                (probe.get('title', ''), probe.get('author', ''), path,
                 probe.get('format', ''), probe.get('size', 0), cover_file))
            self.conn.commit()
            return True

    def remove(self, book_id):
        """移出书库，并级联清理该书的书签与阅读历史（防孤儿数据）"""
        with self.lock:
            row = self.conn.execute(
                'SELECT path FROM books WHERE id=?', (book_id,)).fetchone()
            self.conn.execute('DELETE FROM books WHERE id=?', (book_id,))
            if row:
                self.conn.execute(
                    'DELETE FROM bookmarks WHERE book_path=?', (row['path'],))
                self.conn.execute(
                    'DELETE FROM reading_log WHERE book_path=?', (row['path'],))
            self.conn.commit()

    def all(self, query=''):
        with self.lock:
            if query:
                like = f'%{query}%'
                cur = self.conn.execute(
                    'SELECT * FROM books WHERE title LIKE ? OR author LIKE ? '
                    'ORDER BY added DESC, id DESC', (like, like))
            else:
                cur = self.conn.execute(
                    'SELECT * FROM books ORDER BY added DESC, id DESC')
            return [dict(r) for r in cur.fetchall()]

    def get(self, book_id):
        with self.lock:
            cur = self.conn.execute('SELECT * FROM books WHERE id=?', (book_id,))
            r = cur.fetchone()
            return dict(r) if r else None

    # ---- 阅读进度 ----
    def save_position(self, path, chapter, scroll, title='', chapter_title=''):
        with self.lock:
            self.conn.execute(
                'UPDATE books SET last_chapter=?, last_scroll=? WHERE path=?',
                (chapter, scroll, path))
            # 阅读历史：同章连续记录只保留最新一条
            last = self.conn.execute(
                'SELECT id FROM reading_log WHERE book_path=? AND chapter=? '
                'ORDER BY id DESC LIMIT 1', (path, chapter)).fetchone()
            if last:
                self.conn.execute(
                    'UPDATE reading_log SET scroll=?, ts=datetime("now","localtime") '
                    'WHERE id=?', (scroll, last['id']))
            else:
                self.conn.execute(
                    'INSERT INTO reading_log(book_path, title, chapter, '
                    'chapter_title, scroll) VALUES(?,?,?,?,?)',
                    (path, title, chapter, chapter_title, scroll))
            self.conn.commit()
            # 历史表定期裁剪到最近 5000 条
            rid = self.conn.execute(
                'SELECT MAX(id) FROM reading_log').fetchone()[0] or 0
            if rid % 200 == 0:
                self.conn.execute(
                    'DELETE FROM reading_log WHERE id NOT IN '
                    "(SELECT id FROM reading_log ORDER BY id DESC LIMIT 5000)")
                self.conn.commit()

    def get_position(self, path):
        with self.lock:
            cur = self.conn.execute(
                'SELECT last_chapter, last_scroll FROM books WHERE path=?', (path,))
            r = cur.fetchone()
            return (r['last_chapter'] or 0, r['last_scroll'] or 0) if r else (0, 0)

    # ---- 书签 ----
    def add_bookmark(self, book_path, chapter, scroll, label):
        with self.lock:
            self.conn.execute(
                'INSERT INTO bookmarks(book_path, chapter, scroll, label) '
                'VALUES(?,?,?,?)', (book_path, chapter, scroll, label))
            self.conn.commit()

    def bookmarks(self, book_path):
        with self.lock:
            cur = self.conn.execute(
                'SELECT * FROM bookmarks WHERE book_path=? ORDER BY id DESC',
                (book_path,))
            return [dict(r) for r in cur.fetchall()]

    def remove_bookmark(self, bm_id):
        with self.lock:
            self.conn.execute('DELETE FROM bookmarks WHERE id=?', (bm_id,))
            self.conn.commit()

    # ---- 阅读历史 ----
    def reading_log(self, limit=300):
        with self.lock:
            cur = self.conn.execute(
                'SELECT * FROM reading_log ORDER BY id DESC LIMIT ?', (limit,))
            return [dict(r) for r in cur.fetchall()]
