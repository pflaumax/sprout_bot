"""SQLite storage. One file, one writer, no server: enough for a bot that sees
a few thousand events on a good day and has to survive a Pi reboot."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .events import Node
from .tree import Stats

SCHEMA = """
CREATE TABLE IF NOT EXISTS trees (
    id INTEGER PRIMARY KEY,
    seed_uri TEXT NOT NULL UNIQUE,
    seed_cid TEXT NOT NULL,
    created_at TEXT NOT NULL,
    activated_at TEXT NOT NULL,  -- the newest one is the tree that grows
    last_post_uri TEXT NOT NULL,
    last_post_cid TEXT NOT NULL,
    last_post_at TEXT NOT NULL,
    last_stats TEXT NOT NULL,
    posted_nodes INTEGER NOT NULL DEFAULT 0,
    posted_rowid INTEGER NOT NULL DEFAULT 0  -- nodes after this are not shown yet
);
-- The seed and every update posted under it: likes and reposts of any of
-- them make the tree grow.
CREATE TABLE IF NOT EXISTS tracked_posts (
    uri TEXT PRIMARY KEY,
    tree_id INTEGER NOT NULL REFERENCES trees (id)
);
-- Every record is kept, even a second repost or a like after an unlike, so a
-- `via` pointing at any of them still finds its branch. Counting collapses
-- them to one per person, kind and post (see tree.build_tree).
CREATE TABLE IF NOT EXISTS nodes (
    uri TEXT PRIMARY KEY,
    tree_id INTEGER NOT NULL REFERENCES trees (id),
    kind TEXT NOT NULL,
    actor_did TEXT NOT NULL,
    actor_handle TEXT NOT NULL,
    subject_uri TEXT NOT NULL,
    via_uri TEXT,
    created_at TEXT NOT NULL,
    seen_at TEXT NOT NULL  -- when the bot read it, for how long it has waited
);
CREATE INDEX IF NOT EXISTS nodes_by_tree ON nodes (tree_id);
-- People already tagged as the biggest branch, so a lead changing hands back
-- and forth does not tag them over and over.
CREATE TABLE IF NOT EXISTS tagged (
    tree_id INTEGER NOT NULL REFERENCES trees (id),
    did TEXT NOT NULL,
    PRIMARY KEY (tree_id, did)
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Tree:
    id: int
    seed_uri: str
    seed_cid: str
    last_post_uri: str
    last_post_cid: str
    last_post_at: str
    last_stats: Stats
    posted_nodes: int
    posted_rowid: int


class Store:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def active_tree(self) -> Tree | None:
        row = self.db.execute(
            "SELECT id, seed_uri, seed_cid, last_post_uri, last_post_cid,"
            " last_post_at, last_stats, posted_nodes, posted_rowid"
            " FROM trees ORDER BY activated_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return Tree(*row[:6], Stats.from_json(row[6]), *row[7:])

    def plant(self, seed_uri: str, seed_cid: str, now: str) -> Tree:
        """Makes this seed the growing tree. A seed seen before carries on where
        it stopped; a new one starts with the seed as its last post, so updates
        thread under it. One transaction, so a crash leaves no half a tree."""
        with self.db:
            self.db.execute(
                "INSERT INTO trees (seed_uri, seed_cid, created_at, activated_at,"
                " last_post_uri, last_post_cid, last_post_at, last_stats)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (seed_uri)"
                " DO UPDATE SET activated_at = excluded.activated_at",
                (
                    seed_uri,
                    seed_cid,
                    now,
                    now,
                    seed_uri,
                    seed_cid,
                    now,
                    Stats().to_json(),
                ),
            )
            tree_id = self.db.execute(
                "SELECT id FROM trees WHERE seed_uri = ?", (seed_uri,)
            ).fetchone()[0]
            self.db.execute(
                "INSERT OR IGNORE INTO tracked_posts (uri, tree_id) VALUES (?, ?)",
                (seed_uri, tree_id),
            )
        return self.active_tree()

    def tracked(self, tree_id: int) -> set[str]:
        return {
            r[0]
            for r in self.db.execute(
                "SELECT uri FROM tracked_posts WHERE tree_id = ?", (tree_id,)
            )
        }

    def add(self, tree_id: int, nodes: list[Node], seen_at: str) -> int:
        """Returns how many records were new."""
        with self.db:
            before = self.db.total_changes
            self.db.executemany(
                "INSERT OR IGNORE INTO nodes (uri, tree_id, kind, actor_did,"
                " actor_handle, subject_uri, via_uri, created_at, seen_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        n.uri,
                        tree_id,
                        n.kind,
                        n.actor_did,
                        n.actor_handle,
                        n.subject_uri,
                        n.via_uri,
                        n.created_at,
                        seen_at,
                    )
                    for n in nodes
                ],
            )
            return self.db.total_changes - before

    def nodes(self, tree_id: int) -> list[Node]:
        rows = self.db.execute(
            "SELECT uri, kind, actor_did, actor_handle, subject_uri, via_uri,"
            " created_at FROM nodes WHERE tree_id = ? ORDER BY created_at, uri",
            (tree_id,),
        )
        return [Node(*r) for r in rows]

    def count(self, tree_id: int) -> int:
        """Leaves, branches and flowers, one per person, kind and post: the
        same count build_tree arrives at."""
        return self.db.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT kind, actor_did, subject_uri"
            " FROM nodes WHERE tree_id = ?)",
            (tree_id,),
        ).fetchone()[0]

    def pending_since(self, tree: Tree) -> str | None:
        """When the oldest record not yet shown in a post was read."""
        return self.db.execute(
            "SELECT MIN(seen_at) FROM nodes WHERE tree_id = ? AND rowid > ?",
            (tree.id, tree.posted_rowid),
        ).fetchone()[0]

    def posted(
        self, tree_id: int, uri: str, cid: str, at: str, stats: Stats, node_count: int
    ) -> None:
        """Records an update. The bot is single-threaded, so nothing was read
        between building the picture and this call: every node so far is in it."""
        with self.db:
            self.db.execute(
                "UPDATE trees SET last_post_uri = ?, last_post_cid = ?,"
                " last_post_at = ?, last_stats = ?, posted_nodes = ?,"
                " posted_rowid = (SELECT COALESCE(MAX(rowid), 0) FROM nodes"
                " WHERE tree_id = ?) WHERE id = ?",
                (uri, cid, at, stats.to_json(), node_count, tree_id, tree_id),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO tracked_posts (uri, tree_id) VALUES (?, ?)",
                (uri, tree_id),
            )

    def tagged(self, tree_id: int) -> set[str]:
        rows = self.db.execute("SELECT did FROM tagged WHERE tree_id = ?", (tree_id,))
        return {r[0] for r in rows}

    def tag(self, tree_id: int, did: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO tagged (tree_id, did) VALUES (?, ?)",
                (tree_id, did),
            )

    def get(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value)
            )
