import threading
from dataclasses import replace
from pathlib import Path

import pytest
from helpers import (
    SEED,
    ApiError,
    Clock,
    FakeSky,
    RateLimited,
    like,
    reply,
    repost,
    repost_uri,
)

from sprout import texts
from sprout.bot import MAX_FAILURES, Bot
from sprout.config import Config
from sprout.db import Store


@pytest.fixture
def world(tmp_path: Path):
    cfg = Config(
        handle="sprout.test",
        app_password="x",
        data_dir=tmp_path,
        poll_seconds=30,
        batch=5,
        min_gap_minutes=5,
        max_wait_minutes=30,
        update_avatar=True,
        mention_leader=True,
    )
    sky, clock = FakeSky(), Clock()
    bot = Bot(cfg, Store(cfg.db_path), sky, clock)
    return bot, sky, clock


def text_of(post):
    return texts.plain(post["segments"])


def test_seed_is_posted_as_a_new_thread_with_an_avatar(world):
    bot, sky, _ = world
    tree = bot.plant()
    assert len(sky.posts) == 1
    assert sky.posts[0]["reply_to"] is None
    assert text_of(sky.posts[0]) == texts.SEED
    assert sky.avatars == 1
    assert tree.last_post_uri == tree.seed_uri


def test_nothing_happens_without_people(world):
    bot, sky, clock = world
    bot.adopt("https://bsky.app/profile/sprout.test/post/seed")
    clock.advance(600)
    assert bot.step() is False
    assert sky.posts == []


def test_a_small_batch_waits_for_the_max_wait(world):
    bot, sky, clock = world
    bot.adopt("link")
    sky.inbox = [like("a", 1), like("b", 2)]
    clock.advance(10)
    assert bot.step() is False  # read now; has not waited at all yet
    clock.advance(25)
    assert bot.step() is False
    clock.advance(5)
    assert bot.step() is True  # the oldest has waited the full 30 minutes
    assert text_of(sky.posts[0]).startswith("🌳 +2 листки")


def test_after_a_quiet_spell_a_lone_like_still_waits_for_company(world):
    bot, sky, clock = world
    bot.adopt("link")
    clock.advance(240)  # hours of nothing
    sky.inbox = [like("a", 1)]
    assert bot.step() is False
    clock.advance(10)
    sky.inbox.append(like("b", 2))
    assert bot.step() is False
    clock.advance(20)
    assert bot.step() is True
    assert text_of(sky.posts[0]).startswith("🌳 +2 листки")


def test_nothing_is_loaded_while_the_min_gap_runs(world, monkeypatch):
    bot, sky, clock = world
    bot.adopt("link")
    sky.inbox = [like(w, i) for i, w in enumerate("abcdefgh")]
    loads = []
    real = bot.store.nodes
    monkeypatch.setattr(
        bot.store, "nodes", lambda tree_id: loads.append(1) or real(tree_id)
    )
    clock.advance(1)
    assert bot.step() is False
    assert loads == []


def test_a_full_batch_posts_after_the_min_gap_and_threads_under_the_last_update(world):
    bot, sky, clock = world
    bot.adopt("link")
    sky.inbox = [like(w, i) for i, w in enumerate("abcde")]
    clock.advance(1)
    assert bot.step() is False  # too soon after the seed
    clock.advance(5)
    assert bot.step() is True
    first = sky.posts[0]
    assert first["reply_to"] == ((SEED, "cidseed"), (SEED, "cidseed"))

    sky.inbox += [like(w, 10 + i) for i, w in enumerate("fghij")]
    clock.advance(6)
    assert bot.step() is True
    assert sky.posts[1]["reply_to"] == ((SEED, "cidseed"), (first["uri"], "cid1"))


def test_a_milestone_posts_without_a_full_batch(world):
    bot, sky, clock = world
    bot.cfg = replace(bot.cfg, batch=100)
    bot.adopt("link")
    sky.inbox = [like(f"u{i}", i) for i in range(9)]
    clock.advance(6)
    assert bot.step() is False
    sky.inbox.append(like("u9", 9))  # the tenth leaf
    assert bot.step() is True


def test_the_same_like_twice_is_one_leaf(world):
    bot, sky, clock = world
    tree = bot.adopt("link")
    # Seen twice in overlapping pages, then unliked and liked again.
    sky.inbox = [
        like("a", 1, rkey="one"),
        like("a", 1, rkey="one"),
        like("a", 2, rkey="two"),
    ]
    bot.step()
    assert bot.store.count(tree.id) == 1


def test_likes_on_updates_grow_the_same_tree(world):
    bot, sky, clock = world
    tree = bot.adopt("link")
    sky.inbox = [like(w, i) for i, w in enumerate("abcde")]
    clock.advance(6)
    bot.step()
    update = sky.posts[0]["uri"]
    sky.inbox += [like("a", 20, subject=update), repost("z", 21, subject=update)]
    bot.step()
    assert bot.store.count(tree.id) == 7


def test_new_leader_is_tagged_once(world):
    bot, sky, clock = world
    bot.adopt("link")
    sky.inbox = [repost("alice", 1)] + [
        like(w, 2 + i, via=repost_uri("alice")) for i, w in enumerate("abcd")
    ]
    clock.advance(6)
    bot.step()
    assert ("mention", "@alice.test", "did:plc:alice") in sky.posts[0]["segments"]

    sky.inbox += [
        like(w, 10 + i, via=repost_uri("alice")) for i, w in enumerate("efghi")
    ]
    clock.advance(6)
    bot.step()
    assert all(s[0] == "text" for s in sky.posts[1]["segments"])


def test_replies_count_as_flowers(world):
    bot, sky, clock = world
    bot.adopt("link")
    sky.inbox = [reply(w, i) for i, w in enumerate("abcde")]
    clock.advance(6)
    assert bot.step() is True
    assert "+5 квіток" in text_of(sky.posts[0])


def test_a_failed_avatar_does_not_lose_the_post(world):
    bot, sky, clock = world
    tree = bot.adopt("link")
    sky.avatar_fails = True
    sky.inbox = [like(w, i) for i, w in enumerate("abcde")]
    clock.advance(6)
    assert bot.step() is True
    assert bot.store.active_tree().posted_nodes == 5
    assert bot.store.active_tree().id == tree.id


def test_preview_posts_nothing(world):
    bot, sky, clock = world
    bot.adopt("link")
    sky.inbox = [like("a", 1)]
    path, text = bot.preview()
    assert path.exists()
    assert "+1 листок" in text
    assert sky.posts == []


def test_a_second_repost_by_the_same_person_keeps_the_chain(world):
    bot, sky, clock = world
    tree = bot.adopt("link")
    again = repost("alice", 2)
    again["uri"] = "at://did:plc:alice/app.bsky.feed.repost/again"
    sky.inbox = [
        repost("alice", 1),
        again,  # unreposted and reposted: a new record
        like("bob", 3, via=again["uri"]),
    ]
    bot.step()
    assert bot.store.count(tree.id) == 2  # one branch, one leaf
    path, text = bot.preview()
    assert "1 гілка" in text
    assert "@alice.test" in text  # bob's like counted on alice's branch


def test_lead_changing_hands_tags_each_person_once(world):
    bot, sky, clock = world
    bot.adopt("link")
    sky.inbox = [repost("alice", 1), repost("bob", 2)]
    # Leaves on each branch after every round: 3/0, 3/5, 7/5, 7/9.
    rounds = [("alice", 3), ("bob", 5), ("alice", 4), ("bob", 4)]
    leaders = []
    for i, (who, extra) in enumerate(rounds):
        sky.inbox += [
            like(f"{who}{i}-{k}", 10 * (i + 1) + k, via=repost_uri(who))
            for k in range(extra)
        ]
        bot.poll(bot.store.active_tree())
        clock.advance(31)  # every round waits out max_wait, whatever its size
        assert bot.step() is True
        leaders.append(bot.store.active_tree().last_stats.leader_did)
    assert leaders == ["did:plc:alice", "did:plc:bob", "did:plc:alice", "did:plc:bob"]
    tags = [d for p in sky.posts for d in texts.mentioned(p["segments"])]
    assert tags == ["did:plc:alice", "did:plc:bob"]


def test_adopt_refuses_posts_it_cannot_grow(world):
    bot, sky, _ = world
    sky.resolved["author"] = "did:plc:someone-else"
    with pytest.raises(SystemExit, match="not a post by"):
        bot.adopt("link")
    sky.resolved.update(author=sky.did, reply=True)
    with pytest.raises(SystemExit, match="is a reply"):
        bot.adopt("link")
    assert bot.store.active_tree() is None


def test_adopting_an_old_seed_carries_on_with_it(world):
    bot, sky, clock = world
    old = bot.adopt("link")
    sky.inbox = [like(w, i) for i, w in enumerate("abcde")]
    clock.advance(6)
    bot.step()
    clock.advance(1)
    bot.plant()  # by accident
    assert bot.store.active_tree().id != old.id
    clock.advance(1)
    back = bot.adopt("link")
    assert back.id == old.id
    assert back.posted_nodes == 5
    assert back.last_post_uri == sky.posts[0]["uri"]


def test_a_new_tree_is_ready_to_grow_straight_away(world):
    bot, _, _ = world
    tree = bot.store.plant("at://seed", "cid", "2026-10-17T12:00:00.000Z")
    assert (tree.last_post_uri, tree.last_post_cid) == ("at://seed", "cid")
    assert tree.last_post_at == "2026-10-17T12:00:00.000Z"


def test_preview_files_are_named_for_what_they_hold(world):
    bot, sky, _ = world
    bot.adopt("link")
    path, _ = bot.preview()
    assert path.read_bytes()[:4] == b"\x89PNG"
    assert path.suffix == ".png"


def test_run_gives_up_when_the_session_is_rejected(world):
    bot, sky, clock = world
    bot.adopt("link")
    sky.failures = [ApiError(400, "ExpiredToken")]
    with pytest.raises(SystemExit) as exit:
        bot.run(threading.Event())
    assert exit.value.code == 1


def test_run_gives_up_after_too_many_failures_in_a_row(world, monkeypatch):
    bot, sky, clock = world
    bot.adopt("link")
    sky.failures = [ApiError(502) for _ in range(MAX_FAILURES)]
    stop = threading.Event()
    monkeypatch.setattr(stop, "wait", lambda seconds: None)
    with pytest.raises(SystemExit):
        bot.run(stop)
    assert sky.failures == []


def test_rate_limits_are_waited_out_not_counted(world, monkeypatch):
    bot, sky, clock = world
    bot.adopt("link")
    sky.failures = [RateLimited(60) for _ in range(MAX_FAILURES + 5)]
    stop = threading.Event()
    waits = []

    def wait(seconds):
        waits.append(seconds)
        if not sky.failures:
            stop.set()

    monkeypatch.setattr(stop, "wait", wait)
    bot.run(stop)  # returns normally: never gave up
    assert len(waits) == MAX_FAILURES + 5
    assert all(w >= 30 for w in waits)
