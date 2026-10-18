"""Turns a Bluesky notification into a piece of the tree, or into nothing.

Notifications arrive as plain lexicon JSON (camelCase keys, `$type`), the same
shape the API returns, so this module has no SDK dependency and the tests can
feed it hand-written dicts.
"""

from __future__ import annotations

from dataclasses import dataclass

BRANCH = "branch"  # a repost or a quote
LEAF = "leaf"  # a like
FLOWER = "flower"  # a reply anywhere in the seed's thread

REASONS = ["like", "repost", "reply", "quote"]


@dataclass(frozen=True)
class Node:
    uri: str  # the like/repost/post record that made this node
    kind: str
    actor_did: str
    actor_handle: str
    subject_uri: str  # the tracked post it points at; "" for flowers
    via_uri: str | None  # the repost it was seen through, when the client says so
    created_at: str  # server's indexedAt, not the client-written createdAt


def _uri(ref: object) -> str | None:
    if isinstance(ref, dict):
        uri = ref.get("uri")
        if isinstance(uri, str):
            return uri
    return None


def _quoted_uri(embed: object) -> str | None:
    if not isinstance(embed, dict):
        return None
    kind = embed.get("$type", "")
    if kind == "app.bsky.embed.record":
        return _uri(embed.get("record"))
    if kind == "app.bsky.embed.recordWithMedia":
        inner = embed.get("record")
        return _uri(inner.get("record")) if isinstance(inner, dict) else None
    return None


def parse_notification(n: dict, tracked: set[str], seed_uri: str) -> Node | None:
    """`tracked` holds the seed and every update the bot has posted under it."""
    record = n.get("record") or {}
    author = n.get("author") or {}
    reason = n.get("reason")

    def node(kind: str, subject: str, via: str | None = None) -> Node:
        return Node(
            uri=n["uri"],
            kind=kind,
            actor_did=author["did"],
            actor_handle=author.get("handle", ""),
            subject_uri=subject,
            via_uri=via,
            created_at=n["indexedAt"],
        )

    if reason in ("like", "repost"):
        subject = _uri(record.get("subject"))
        if subject not in tracked:
            return None
        return node(
            LEAF if reason == "like" else BRANCH, subject, _uri(record.get("via"))
        )

    if reason == "quote":
        subject = _quoted_uri(record.get("embed"))
        if subject not in tracked:
            return None
        return node(BRANCH, subject)

    if reason == "reply":
        reply = record.get("reply")
        root = _uri(reply.get("root")) if isinstance(reply, dict) else None
        if root != seed_uri:
            return None
        return node(FLOWER, "")

    return None
