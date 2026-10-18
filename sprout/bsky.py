"""The only module that talks to Bluesky.

What it hands back is plain lexicon JSON or plain tuples, so the rest of the
bot can be tested with a fake that has the same four methods.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from atproto import Client, SessionEvent, client_utils, models
from atproto_client.exceptions import RequestErrorBase
from atproto_client.models.utils import get_model_as_dict

from .errors import is_auth_error, xrpc_error
from .events import REASONS

log = logging.getLogger(__name__)

PROFILE = {"collection": "app.bsky.actor.profile", "rkey": "self"}
POST_URL = re.compile(r"https://bsky\.app/profile/([^/]+)/post/([^/?#]+)")

Ref = tuple[str, str]  # (uri, cid)

FIRST_RUN_PAGES = 10  # with nothing seen yet, the last 1,000 are plenty
CATCH_UP_PAGES = 100  # after downtime, read back this far to where we stopped


def _time(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


class Bluesky:
    def __init__(self, handle: str, app_password: str, session_file: Path):
        self.client = Client()
        self.session_file = session_file
        self.client.on_session_change(self._keep_session)
        # Logging in is limited to a few hundred times a day, so a restart
        # reuses the saved session instead of creating a new one.
        saved = session_file.read_text().strip() if session_file.exists() else ""
        if saved and not self._resume(saved, handle):
            saved = ""
        if not saved:
            try:
                self.client.login(handle, app_password)
            except RequestErrorBase as e:
                if is_auth_error(e):
                    # Exit code 78 (EX_CONFIG) tells systemd not to retry a
                    # wrong password into Bluesky's login limit.
                    log.error("Bluesky refused the handle or app password: %s", e)
                    raise SystemExit(78) from e
                raise
        self.did = self.client.me.did
        self.handle = self.client.me.handle

    def _resume(self, saved: str, handle: str) -> bool:
        try:
            self.client.login(session_string=saved)
        except Exception:  # expired, revoked, or a file cut short by a crash
            log.info("saved session no longer works, logging in again")
            return False
        wanted = handle.lower().lstrip("@")
        if wanted not in (self.client.me.handle.lower(), self.client.me.did):
            log.info("saved session is for @%s, not @%s", self.client.me.handle, wanted)
            self.client = Client()
            self.client.on_session_change(self._keep_session)
            return False
        return True

    def _keep_session(self, event: SessionEvent, session) -> None:
        if event not in (SessionEvent.CREATE, SessionEvent.REFRESH):
            return
        # Never world-readable, not even for a moment, and never half-written.
        tmp = self.session_file.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(session.encode())
        os.replace(tmp, self.session_file)

    def notifications(self, newer_than: str | None) -> list[dict]:
        """Likes, reposts, replies and quotes, oldest first, reading back until
        a page reaches `newer_than`. Some already seen ones come back too; the
        caller dedupes by URI."""
        found: list[dict] = []
        cursor = None
        for _ in range(CATCH_UP_PAGES if newer_than else FIRST_RUN_PAGES):
            page = self.client.app.bsky.notification.list_notifications(
                params={"limit": 100, "reasons": REASONS, "cursor": cursor}
            )
            batch = [get_model_as_dict(n) for n in page.notifications]
            found += batch
            if not batch or not page.cursor:
                break
            if newer_than and _time(batch[-1]["indexedAt"]) < _time(newer_than):
                break
            cursor = page.cursor
        else:
            if newer_than:
                log.warning(
                    "more than %d notifications since %s; older ones are skipped",
                    100 * CATCH_UP_PAGES,
                    newer_than,
                )
        return found[::-1]

    def post(
        self,
        segments: list[tuple],
        image: bytes,
        alt: str,
        size: tuple[int, int],
        reply_to: tuple[Ref, Ref] | None = None,
    ) -> Ref:
        """`reply_to` is (root, parent)."""
        text = client_utils.TextBuilder()
        for seg in segments:
            if seg[0] == "mention":
                text.mention(seg[1], seg[2])
            else:
                text.text(seg[1])
        reply = None
        if reply_to:
            root, parent = (
                models.ComAtprotoRepoStrongRef.Main(uri=u, cid=c) for u, c in reply_to
            )
            reply = models.AppBskyFeedPost.ReplyRef(root=root, parent=parent)
        res = self.client.send_image(
            text=text,
            image=image,
            image_alt=alt,
            reply_to=reply,
            langs=["uk"],
            image_aspect_ratio=models.AppBskyEmbedDefs.AspectRatio(
                width=size[0], height=size[1]
            ),
        )
        return res.uri, res.cid

    def set_avatar(self, image: bytes) -> None:
        blob = self.client.upload_blob(image).blob
        try:
            current = self.client.com.atproto.repo.get_record(
                params={"repo": self.did, **PROFILE}
            )
            record, swap = get_model_as_dict(current.value), current.cid
        except RequestErrorBase as e:
            # A fresh account may have no profile record yet. Anything else
            # (a timeout, a 5xx) must not be mistaken for that, or the put
            # below would wipe the name, description and labels.
            if xrpc_error(e) != "RecordNotFound":
                raise
            record, swap = {"$type": "app.bsky.actor.profile"}, None
        # Everything else in the profile goes back exactly as it was.
        record["avatar"] = get_model_as_dict(blob)
        data = {"repo": self.did, **PROFILE, "record": record}
        if swap:
            data["swapRecord"] = swap
        self.client.com.atproto.repo.put_record(data=data)

    def resolve_post(self, link: str) -> dict:
        """A bsky.app link or an at:// URI, to the post's uri, cid, author DID
        and whether it is a reply."""
        match = POST_URL.match(link.strip())
        if match:
            actor, rkey = match.groups()
            did = (
                actor
                if actor.startswith("did:")
                else self.client.resolve_handle(actor).did
            )
            link = f"at://{did}/app.bsky.feed.post/{rkey}"
        posts = self.client.get_posts([link]).posts
        if not posts:
            raise ValueError(f"no such post: {link}")
        post = posts[0]
        return {
            "uri": post.uri,
            "cid": post.cid,
            "author": post.author.did,
            "reply": bool(getattr(post.record, "reply", None)),
        }
