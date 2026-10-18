from helpers import SEED, like, reply, repost, repost_uri

from sprout.events import parse_notification
from sprout.tree import Stats, build_tree, stats_of


def grow(*notes):
    return build_tree([parse_notification(n, {SEED}, SEED) for n in notes])


def branch_of(trunk, who):
    return next(
        b for b in trunk.walk() if b.node and b.node.actor_did == f"did:plc:{who}"
    )


def test_reposts_of_reposts_make_a_chain():
    trunk = grow(
        repost("alice", 1),
        repost("bob", 2, via=repost_uri("alice")),
        repost("carol", 3, via=repost_uri("bob")),
    )
    carol = branch_of(trunk, "carol")
    assert carol.depth == 3
    assert carol.parent is branch_of(trunk, "bob")
    assert stats_of(trunk).depth == 3


def test_likes_hang_on_the_branch_they_came_through():
    trunk = grow(
        repost("alice", 1), like("bob", 2, via=repost_uri("alice")), like("dan", 3)
    )
    assert [n.actor_did for n in branch_of(trunk, "alice").leaves] == ["did:plc:bob"]
    assert [n.actor_did for n in trunk.leaves] == ["did:plc:dan"]


def test_a_via_we_never_saw_falls_back_to_the_trunk():
    trunk = grow(
        like("bob", 2, via=repost_uri("ghost")),
        repost("carol", 3, via=repost_uri("ghost")),
    )
    assert len(trunk.leaves) == 1
    assert branch_of(trunk, "carol").parent is trunk


def test_via_cannot_point_into_the_future():
    # A hand-made record could claim to come through a later repost.
    trunk = grow(
        repost("bob", 1, via=repost_uri("alice")),
        repost("alice", 2, via=repost_uri("bob")),
        like("eve", 0, via=repost_uri("alice")),
    )
    assert branch_of(trunk, "bob").parent is trunk
    assert branch_of(trunk, "alice").parent is branch_of(trunk, "bob")
    assert [n.actor_did for n in trunk.leaves] == ["did:plc:eve"]


def test_replies_bloom_on_the_repliers_own_branch():
    trunk = grow(repost("alice", 1), reply("alice", 2), reply("zed", 3))
    assert len(branch_of(trunk, "alice").flowers) == 1
    assert len(trunk.flowers) == 1


def test_leader_counts_leaves_on_the_whole_branch_and_ties_go_to_the_older():
    trunk = grow(
        repost("alice", 1),
        repost("bob", 2),
        repost("carol", 3, via=repost_uri("alice")),
        like("x", 4, via=repost_uri("carol")),
        like("y", 5, via=repost_uri("bob")),
    )
    stats = stats_of(trunk)
    assert (stats.leaves, stats.branches, stats.depth) == (2, 3, 2)
    assert stats.leader_did == "did:plc:alice"  # 1 leaf via carol, and older than bob
    assert stats.leader_leaves == 1


def test_stats_survive_json():
    stats = stats_of(grow(repost("alice", 1), like("bob", 2, via=repost_uri("alice"))))
    assert Stats.from_json(stats.to_json()) == stats
    assert Stats.from_json(None) == Stats()


def test_a_second_repost_is_the_same_branch_and_keeps_its_likes():
    again = repost("alice", 2)
    again["uri"] = "at://did:plc:alice/app.bsky.feed.repost/again"
    trunk = grow(repost("alice", 1), again, like("bob", 3, via=again["uri"]))
    alice = branch_of(trunk, "alice")
    assert [b for b in trunk.walk() if b.node] == [alice]
    assert [n.actor_did for n in alice.leaves] == ["did:plc:bob"]


def test_repeat_likes_and_replies_count_once():
    trunk = grow(
        like("bob", 1, rkey="a"),
        like("bob", 2, rkey="b"),
        reply("zed", 3),
        reply("zed", 4),
    )
    stats = stats_of(trunk)
    assert (stats.leaves, stats.flowers) == (1, 1)
