"""A made-up spread, for seeing how the tree looks at 10 or 10 000 events
without waiting for real people. Nothing here touches Bluesky.

Each repost opens a new audience: most people have few followers, a few have
many, and every audience tires as it gets used up. That is enough to give the
lopsided shape real cascades have.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from .events import BRANCH, FLOWER, LEAF, Node

SEED_URI = "at://did:plc:sprout/app.bsky.feed.post/seed"


def simulate(events: int, seed: int = 1) -> list[Node]:
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    sources: list[str | None] = [None]  # None is the seed itself: the bot's followers
    weights = [40.0]
    reposters: list[tuple[str, str]] = []
    nodes = []

    for i in range(events):
        when = (start + timedelta(seconds=7 * i)).isoformat().replace("+00:00", "Z")
        src = rng.choices(range(len(sources)), weights=weights)[0]
        weights[src] *= 0.97
        via = sources[src]
        did, handle = f"did:plc:sim{i:06d}", f"user{i}.bsky.social"
        roll = rng.random()
        if roll < 0.13:
            uri = f"at://{did}/app.bsky.feed.repost/{i}"
            nodes.append(Node(uri, BRANCH, did, handle, SEED_URI, via, when))
            sources.append(uri)
            weights.append(rng.paretovariate(1.3) * 6)
            reposters.append((did, handle))
        elif roll < 0.94:
            # Some clients do not record `via`; those likes land on the trunk.
            seen_via = None if rng.random() < 0.1 else via
            nodes.append(
                Node(
                    f"at://{did}/app.bsky.feed.like/{i}",
                    LEAF,
                    did,
                    handle,
                    SEED_URI,
                    seen_via,
                    when,
                )
            )
        else:
            if reposters and rng.random() < 0.4:
                did, handle = rng.choice(reposters)
            nodes.append(
                Node(
                    f"at://{did}/app.bsky.feed.post/{i}",
                    FLOWER,
                    did,
                    handle,
                    "",
                    None,
                    when,
                )
            )
    return nodes
