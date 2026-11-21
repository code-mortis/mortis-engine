"""Private local run history, immutable task inputs and write-ahead inference accounting."""

import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def private_directory(path):
    path = Path(os.path.abspath(path))
    if sys.platform == "darwin" and len(path.parts) > 1 and path.parts[1] in {"tmp", "var"}:
        # These system aliases are owned by root and cannot be replaced by a local user.
        alias = Path(*path.parts[:2])
        if alias.is_symlink() and alias.readlink() == Path("private", alias.name):
            path = Path("/private", *path.parts[1:])
    uid = os.getuid() if hasattr(os, "getuid") else None
    for parent in reversed(path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            parent.mkdir(mode=0o700, exist_ok=True)
            info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or (
            uid is not None and (
                info.st_uid not in {0, uid}
                or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)
            )
        ):
            raise ValueError("Mortis state requires private, trusted parent directories without symbolic links.")
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077
            or uid is not None and info.st_uid != uid):
        raise ValueError("Mortis state requires a private directory owned by the current user.")
    return path


class History:
    def __init__(self, repository):
        from .review.source import find_repository

        git_dir = find_repository(repository)
        if git_dir is None:
            raise ValueError("Choose a local Git repository with --repo.")
        root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        if not root.is_absolute():
            raise ValueError("XDG_STATE_HOME must be an absolute path.")
        self.repository = str(git_dir.resolve())
        identity = hashlib.sha256(os.fsencode(self.repository)).hexdigest()
        directory = Path(repository).resolve()
        worktree = next((p for p in (directory, *directory.parents) if (p / ".git").exists()), git_dir)
        if (root / "mortis").resolve().is_relative_to(worktree):
            raise ValueError("Store Mortis state outside the reviewed repository.")
        self.root = private_directory(private_directory(root / "mortis") / identity)
        self.objects = self.root / "objects"
        path = self.root / "history.sqlite3"
        if path.is_symlink():
            raise ValueError("Mortis history must be a private regular file.")
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError("Mortis history requires a private regular file.")
        finally:
            os.close(descriptor)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, scope TEXT NOT NULL,
                started REAL NOT NULL, plan TEXT NOT NULL, report TEXT
            );
            CREATE INDEX IF NOT EXISTS runs_scope ON runs(kind, scope, started);
            CREATE TABLE IF NOT EXISTS assessment_inputs (
                run TEXT NOT NULL, candidate TEXT NOT NULL, input TEXT NOT NULL,
                PRIMARY KEY (run, candidate)
            );
            CREATE TABLE IF NOT EXISTS triage_events (
                kind TEXT NOT NULL, scope TEXT NOT NULL, finding TEXT NOT NULL,
                version INTEGER NOT NULL, run TEXT NOT NULL, previous TEXT NOT NULL,
                state TEXT NOT NULL, reason TEXT NOT NULL, created REAL NOT NULL,
                PRIMARY KEY (kind, scope, finding, version)
            );
            CREATE TABLE IF NOT EXISTS attempts (
                run TEXT NOT NULL, stage TEXT NOT NULL, task TEXT NOT NULL, input TEXT NOT NULL,
                reserved INTEGER NOT NULL, record TEXT NOT NULL, result TEXT,
                PRIMARY KEY (run, stage, task)
            );
            COMMIT;
        """)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.db.close()

    def row(self, run_id, kind=None):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None or kind is not None and row["kind"] != kind:
            raise ValueError("No matching saved run in this repository. See mortis runs.")
        return row

    def list(self):
        rows = self.db.execute("SELECT id, kind, scope, started, report IS NOT NULL AS finished FROM runs ORDER BY started DESC")
        return [dict(row) for row in rows]

    def previous_ids(self, kind, scope=None):
        return [row[0] for row in self.db.execute("""
            SELECT id FROM runs WHERE kind=? AND (scope=? OR ? IS NULL)
            AND report IS NOT NULL ORDER BY started, id""", (kind, scope, scope))]

    def report(self, run_id):
        value = self.row(run_id)["report"]
        if value is None:
            raise ValueError("This run has no final report. Resume it using its run ID.")
        return json.loads(value)

    @contextmanager
    def claim(self, run_id):
        if not re.fullmatch("[a-f0-9]{32}", run_id):
            raise ValueError("Invalid local run ID.")
        path = self.root / (run_id + ".lock")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "r+b") as stream:
            try:
                if os.name == "nt":
                    import msvcrt
                    if not os.fstat(fd).st_size:
                        stream.write(b"0")
                        stream.flush()
                    stream.seek(0)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError("Another Mortis process owns this run.") from exc
            yield

    def begin(self, kind, scope, plan):
        run_id = uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, NULL)",
                            (run_id, kind, scope, time.time(), encode(plan)))
        return run_id

    def finish(self, run_id, report):
        with self.db:
            cursor = self.db.execute("UPDATE runs SET report=? WHERE id=? AND report IS NULL",
                                     (encode(report), run_id))
            if cursor.rowcount != 1:
                raise ValueError("The saved run already has a final report.")

    def attempts(self, run_id):
        return {(row["stage"], row["task"]): (row["input"], json.loads(row["record"]), row["result"])
                for row in self.db.execute("SELECT * FROM attempts WHERE run=? ORDER BY rowid", (run_id,))}

    def start_attempt(self, run_id, record, input_hash):
        with self.db:
            self.db.execute("INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, NULL)", (
                run_id, record["stage"], record["task_key"], input_hash, record["reserved_tokens"], encode(record)))

    def settle(self, run_id, record, result=None):
        with self.db:
            self.db.execute("UPDATE attempts SET record=?, result=? WHERE run=? AND stage=? AND task=?",
                            (encode(record), encode(result) if result is not None else None,
                             run_id, record["stage"], record["task_key"]))

    def save_assessment_input(self, run_id, candidate_id, value):
        encoded = encode(value)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO assessment_inputs VALUES (?, ?, ?)",
                            (run_id, candidate_id, encoded))
            row = self.db.execute("SELECT input FROM assessment_inputs WHERE run=? AND candidate=?",
                                  (run_id, candidate_id)).fetchone()
            if row[0] != encoded:
                raise ValueError("The saved candidate has different immutable assessment inputs.")

    def assessment_input(self, run_id, candidate_id):
        row = self.db.execute("SELECT input FROM assessment_inputs WHERE run=? AND candidate=?",
                              (run_id, candidate_id)).fetchone()
        return json.loads(row["input"]) if row is not None else None

    def assessment_inputs(self, run_id):
        return {row["candidate"]: json.loads(row["input"]) for row in self.db.execute(
            "SELECT candidate, input FROM assessment_inputs WHERE run=? ORDER BY rowid", (run_id,))}

    def assessment_dependencies(self, run_id):
        rows = self.db.execute("SELECT candidate, input FROM assessment_inputs WHERE run=? ORDER BY rowid", (run_id,))
        return {row["candidate"]: dependencies for row in rows
                if (dependencies := json.loads(row["input"]).get("predecessors")) is not None}
