"""Hand-written notifications in the shape the API returns them, and a fake
Bluesky that records what the bot would have posted."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

SEED = "at://did:plc:bot/app.bsky.feed.post/seed"
START = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def stamp(seconds: float) -> str:
    moment = START + timedelta(seconds=seconds)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _note(reason: str, uri: str, who: str, at: float, record: dict) -> dict:
    return {
        "uri": uri,
        "cid": "bafy" + who,
        "reason": reason,
        "indexedAt": stamp(at),
        "isRead": False,
        "author": {"did": f"did:plc:{who}", "handle": f"{who}.test"},
        "record": record,
    }


def _ref(uri: str) -> dict:
    return {"$type": "com.atproto.repo.strongRef", "uri": uri, "cid": "bafyref"}


def like(
    who: str, at: float, subject: str = SEED, via: str | None = None, rkey: str = ""
) -> dict:
    record = {
        "$type": "app.bsky.feed.like",
        "subject": _ref(subject),
        "createdAt": stamp(at),
    }
    if via:
        record["via"] = _ref(via)
    return _note(
        "like", f"at://did:plc:{who}/app.bsky.feed.like/{rkey or at}", who, at, record
    )


def repost(who: str, at: float, subject: str = SEED, via: str | None = None) -> dict:
    record = {
        "$type": "app.bsky.feed.repost",
        "subject": _ref(subject),
        "createdAt": stamp(at),
    }
    if via:
        record["via"] = _ref(via)
    return _note("repost", repost_uri(who), who, at, record)


def repost_uri(who: str) -> str:
    return f"at://did:plc:{who}/app.bsky.feed.repost/r"


def reply(who: str, at: float, root: str = SEED) -> dict:
    record = {
        "$type": "app.bsky.feed.post",
        "text": "гарне дерево",
        "reply": {"root": _ref(root), "parent": _ref(root)},
        "createdAt": stamp(at),
    }
    return _note(
        "reply", f"at://did:plc:{who}/app.bsky.feed.post/{at}", who, at, record
    )


def quote(who: str, at: float, subject: str = SEED, with_media: bool = False) -> dict:
    embed = {"$type": "app.bsky.embed.record", "record": _ref(subject)}
    if with_media:
        embed = {
            "$type": "app.bsky.embed.recordWithMedia",
            "record": embed,
            "media": {},
        }
    record = {
        "$type": "app.bsky.feed.post",
        "text": "дивіться",
        "embed": embed,
        "createdAt": stamp(at),
    }
    return _note(
        "quote", f"at://did:plc:{who}/app.bsky.feed.post/q{at}", who, at, record
    )


class FakeSky:
    did = "did:plc:bot"
    handle = "sprout.test"

    def __init__(self):
        self.inbox: list[dict] = []  # oldest first, like Bluesky.notifications
        self.posts: list[dict] = []
        self.avatars = 0
        self.avatar_fails = False
        self.failures: list[Exception] = []  # raised by the next polls, in order
        self.resolved = {
            "uri": SEED,
            "cid": "cidseed",
            "author": self.did,
            "reply": False,
        }

    def notifications(self, newer_than):
        if self.failures:
            raise self.failures.pop(0)
        return list(self.inbox)

    def post(self, segments, image, alt, size, reply_to=None):
        uri = f"at://did:plc:bot/app.bsky.feed.post/{len(self.posts) + 1}"
        self.posts.append(
            {
                "uri": uri,
                "segments": segments,
                "image": image,
                "alt": alt,
                "size": size,
                "reply_to": reply_to,
            }
        )
        return uri, f"cid{len(self.posts)}"

    def set_avatar(self, image):
        if self.avatar_fails:
            raise RuntimeError("upload failed")
        self.avatars += 1

    def resolve_post(self, link):
        return dict(self.resolved)


class ApiError(Exception):
    """Shaped like the SDK's request errors: a response with a status code
    and an XRPC error body."""

    def __init__(self, status: int, error: str = ""):
        super().__init__(f"{status} {error}")
        self.response = SimpleNamespace(status_code=status, content={"error": error})


class RateLimited(Exception):
    def __init__(self, seconds: float):
        super().__init__("429")
        self.reset_at = datetime.now(UTC) + timedelta(seconds=seconds)


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now

    def advance(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)
