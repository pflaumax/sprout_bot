"""The tree as a structure: which branch hangs off which, and what grows on it.

The trunk is the seed post. A repost becomes a branch on the branch it was
seen through (`via`), or on the trunk when the client did not say. Likes hang
on the branch they came through, replies bloom on the replier's own branch.

`via` is written by the client, so it is a hint, not a fact: a branch may only
hang off a branch that existed before it. That one rule rules out cycles and
any attempt to graft onto the future.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from .events import BRANCH, FLOWER, LEAF, Node


@dataclass(eq=False)
class Branch:
    node: Node | None  # None for the trunk
    parent: Branch | None = None
    children: list[Branch] = field(default_factory=list)
    leaves: list[Node] = field(default_factory=list)
    flowers: list[Node] = field(default_factory=list)
    depth: int = 0
    leaf_total: int = 0  # leaves on this branch and everything above it

    @property
    def key(self) -> str:
        return self.node.uri if self.node else "trunk"

    def walk(self):
        """Parents before children."""
        stack = [self]
        while stack:
            b = stack.pop()
            yield b
            stack.extend(reversed(b.children))


@dataclass(frozen=True)
class Stats:
    leaves: int = 0
    branches: int = 0
    flowers: int = 0
    depth: int = 0  # the longest chain of reposts of reposts
    leader_did: str | None = None
    leader_handle: str | None = None
    leader_leaves: int = 0

    @property
    def total(self) -> int:
        return self.leaves + self.branches + self.flowers

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str | None) -> Stats:
        return cls(**json.loads(raw)) if raw else cls()


def _order(n: Node) -> tuple[str, str]:
    return (n.created_at, n.uri)


def build_tree(nodes: list[Node]) -> Branch:
    """Each person counts once per kind and post: a second repost (or a
    quote after a repost) of the same post is an alias of their branch, so
    likes that came through either land on it; a like after an unlike, or a
    second reply, adds nothing."""
    trunk = Branch(node=None)
    by_uri: dict[str, tuple[Branch, Node]] = {}  # repost record -> its branch
    own: dict[tuple[str, str], Branch] = {}  # (person, post) -> their branch
    first_of_actor: dict[str, Branch] = {}

    def through(n: Node) -> Branch:
        """The branch `n` came via. Anything in by_uri is older than a branch
        being placed; a leaf must also be younger than the repost it names."""
        branch, record = by_uri.get(n.via_uri or "", (trunk, None))
        if record is not None and _order(record) > _order(n):
            return trunk
        return branch

    for n in sorted((n for n in nodes if n.kind == BRANCH), key=_order):
        key = (n.actor_did, n.subject_uri)
        if key in own:
            by_uri[n.uri] = (own[key], n)
            continue
        parent = through(n)
        b = Branch(node=n, parent=parent, depth=parent.depth + 1)
        parent.children.append(b)
        by_uri[n.uri] = (b, n)
        own[key] = b
        first_of_actor.setdefault(n.actor_did, b)

    seen: set[tuple[str, str, str]] = set()
    for n in sorted((n for n in nodes if n.kind != BRANCH), key=_order):
        key = (n.kind, n.actor_did, n.subject_uri)
        if key in seen:
            continue
        seen.add(key)
        if n.kind == LEAF:
            through(n).leaves.append(n)
        elif n.kind == FLOWER:
            first_of_actor.get(n.actor_did, trunk).flowers.append(n)

    for b in reversed(list(trunk.walk())):
        b.leaf_total = len(b.leaves) + sum(c.leaf_total for c in b.children)
    return trunk


def stats_of(trunk: Branch) -> Stats:
    every = list(trunk.walk())
    branches = [b for b in every if b.node is not None]
    # On a tie the older branch keeps the lead, so it does not flap between posts.
    leader = min(
        (b for b in branches if b.leaf_total > 0),
        key=lambda b: (-b.leaf_total, _order(b.node)),
        default=None,
    )
    return Stats(
        leaves=sum(len(b.leaves) for b in every),
        branches=len(branches),
        flowers=sum(len(b.flowers) for b in every),
        depth=max((b.depth for b in branches), default=0),
        leader_did=leader.node.actor_did if leader else None,
        leader_handle=leader.node.actor_handle if leader else None,
        leader_leaves=leader.leaf_total if leader else 0,
    )
