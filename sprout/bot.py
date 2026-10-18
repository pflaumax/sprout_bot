"""The loop: read notifications, grow the tree, and now and then show it.

The bot never speaks first. Every post it makes is an answer to people having
liked, reposted or replied; with nobody around it stays silent forever.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import texts
from .config import Config
from .db import Store, Tree
from .errors import is_auth_error
from .events import parse_notification
from .render import encode, extension, render
from .tree import Stats, build_tree, stats_of

log = logging.getLogger(__name__)

IMAGE_SIZE = 1600
AVATAR_SIZE = 800
MAX_BACKOFF = 900  # seconds
MAX_FAILURES = 10  # in a row; an hour and a half of backing off
MILESTONES = (
    10,
    25,
    50,
    100,
    250,
    500,
    1000,
    2500,
    5000,
    10_000,
    25_000,
    50_000,
    100_000,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    return (
        moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def parse_time(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def milestone(before: Stats, now: Stats) -> bool:
    """A round number of leaves crossed, or a longer chain of reposts."""
    crossed = any(before.leaves < m <= now.leaves for m in MILESTONES)
    return crossed or now.depth > before.depth


class Bot:
    def __init__(self, cfg: Config, store: Store, sky, clock=utcnow):
        self.cfg = cfg
        self.store = store
        self.sky = sky
        self.clock = clock
        self._checked = None  # (tree id, count) last looked at for a milestone

    def plant(self, text: str = texts.SEED) -> Tree:
        """Posts a new seed. The previous tree, if any, stops growing."""
        rendered = render(build_tree([]), IMAGE_SIZE)
        uri, cid = self.sky.post(
            [("text", text)],
            encode(rendered.image),
            texts.alt(Stats()),
            rendered.image.size,
        )
        tree = self.store.plant(uri, cid, iso(self.clock()))
        self._avatar(rendered)
        log.info("planted %s", uri)
        return tree

    def adopt(self, link: str) -> Tree:
        """Grows a tree on a post that already exists: one written by hand, or
        an older seed to carry on with."""
        post = self.sky.resolve_post(link)
        if post["author"] != self.sky.did:
            # Bluesky only notifies the author, so the tree could never grow.
            raise SystemExit(f"{post['uri']} is not a post by @{self.sky.handle}.")
        if post["reply"]:
            raise SystemExit(f"{post['uri']} is a reply; a seed has to start a thread.")
        log.info("adopted %s", post["uri"])
        return self.store.plant(post["uri"], post["cid"], iso(self.clock()))

    def poll(self, tree: Tree) -> int:
        batch = self.sky.notifications(self.store.get("seen_until"))
        tracked = self.store.tracked(tree.id)
        nodes = [
            n
            for n in (parse_notification(x, tracked, tree.seed_uri) for x in batch)
            if n
        ]
        added = self.store.add(tree.id, nodes, iso(self.clock()))
        if batch:
            self.store.set("seen_until", batch[-1]["indexedAt"])
        if added:
            log.info("%d new on the tree", added)
        return added

    def step(self) -> bool:
        """One poll, and an update post if one is due. Returns whether it posted.

        An update is due once the last one is at least min_gap old and either
        a full batch is waiting, the oldest waiting thing has waited max_wait,
        or a milestone was reached. The checks run cheapest first, so most
        polls never load the tree."""
        tree = self._tree()
        self.poll(tree)
        count = self.store.count(tree.id)
        pending = count - tree.posted_nodes
        now = self.clock()
        if pending <= 0 or now - parse_time(tree.last_post_at) < self._gap:
            return False
        since = self.store.pending_since(tree)
        waited = now - parse_time(since) if since else timedelta(0)
        if pending < self.cfg.batch and waited < self._wait:
            if self._checked == (tree.id, count):
                return False  # nothing new since the last look
            self._checked = (tree.id, count)
            trunk = build_tree(self.store.nodes(tree.id))
            if not milestone(tree.last_stats, stats_of(trunk)):
                return False
        else:
            trunk = build_tree(self.store.nodes(tree.id))
        self._post_update(tree, trunk, count)
        return True

    def _post_update(self, tree: Tree, trunk, count: int) -> None:
        stats = stats_of(trunk)
        rendered = render(trunk, IMAGE_SIZE)
        segments = texts.update(stats, tree.last_stats, self._may_tag(tree, stats))
        root, parent = (
            (tree.seed_uri, tree.seed_cid),
            (tree.last_post_uri, tree.last_post_cid),
        )
        uri, cid = self.sky.post(
            segments,
            encode(rendered.image),
            texts.alt(stats),
            rendered.image.size,
            reply_to=(root, parent),
        )
        self.store.posted(tree.id, uri, cid, iso(self.clock()), stats, count)
        for did in texts.mentioned(segments):
            self.store.tag(tree.id, did)
        log.info(
            "posted %s: %d leaves, %d branches, %d flowers",
            uri,
            stats.leaves,
            stats.branches,
            stats.flowers,
        )
        self._avatar(rendered)

    def _may_tag(self, tree: Tree, stats: Stats) -> bool:
        """Tag a new leader only once per tree, however often the lead flips."""
        if not self.cfg.mention_leader or not stats.leader_did:
            return False
        return stats.leader_did not in self.store.tagged(tree.id)

    def preview(self) -> tuple[Path, str]:
        """Reads new notifications and writes what the next update would look
        like into the out folder, without posting anything."""
        tree = self._tree()
        self.poll(tree)
        trunk = build_tree(self.store.nodes(tree.id))
        stats = stats_of(trunk)
        rendered = render(trunk, IMAGE_SIZE)
        self.cfg.out_dir.mkdir(parents=True, exist_ok=True)
        # Through encode(), as uploads go: it may well be a JPEG.
        image = encode(rendered.image)
        image_path = self.cfg.out_dir / f"preview.{extension(image)}"
        image_path.write_bytes(image)
        avatar = encode(rendered.avatar(AVATAR_SIZE))
        (self.cfg.out_dir / f"preview-avatar.{extension(avatar)}").write_bytes(avatar)
        segments = texts.update(stats, tree.last_stats, self._may_tag(tree, stats))
        return image_path, texts.plain(segments)

    def run(self, stop: threading.Event) -> None:
        """Polls until stopped. Gives up (exit code 1, so systemd restarts it
        with a fresh login) when Bluesky rejects the session, or after
        MAX_FAILURES failed steps in a row."""
        wait, failures = self.cfg.poll_seconds, 0
        while not stop.is_set():
            try:
                self.step()
                wait, failures = self.cfg.poll_seconds, 0
            except Exception as e:  # keep growing through network trouble
                reset = getattr(e, "reset_at", None)  # the SDK's rate limit error
                if reset:
                    # Waiting it out is the fix; a restart would only log in again.
                    wait = max(
                        self.cfg.poll_seconds, (reset - utcnow()).total_seconds()
                    )
                    log.warning("rate limited, waiting %.0f s", wait)
                    stop.wait(wait)
                    continue
                failures += 1
                if is_auth_error(e) or failures >= MAX_FAILURES:
                    log.exception("giving up after %d failed step(s)", failures)
                    raise SystemExit(1) from e
                wait = min(wait * 2, MAX_BACKOFF)
                log.exception("step failed, retrying in %.0f s", wait)
            stop.wait(wait)

    @property
    def _gap(self) -> timedelta:
        return timedelta(minutes=self.cfg.min_gap_minutes)

    @property
    def _wait(self) -> timedelta:
        return timedelta(minutes=self.cfg.max_wait_minutes)

    def _tree(self) -> Tree:
        tree = self.store.active_tree()
        if tree is None:
            raise SystemExit(
                "No tree yet: run `python -m sprout seed`"
                " or `python -m sprout adopt <link>` first."
            )
        return tree

    def _avatar(self, rendered) -> None:
        if not self.cfg.update_avatar:
            return
        try:
            self.sky.set_avatar(encode(rendered.avatar(AVATAR_SIZE)))
        except Exception:
            log.exception("could not update the avatar")
