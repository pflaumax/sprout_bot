from helpers import SEED, like, quote, reply, repost, repost_uri

from sprout.events import BRANCH, FLOWER, LEAF, parse_notification

TRACKED = {SEED}


def test_like_keeps_the_repost_it_came_through():
    node = parse_notification(like("bob", 5, via=repost_uri("alice")), TRACKED, SEED)
    assert node.kind == LEAF
    assert node.actor_did == "did:plc:bob"
    assert node.actor_handle == "bob.test"
    assert node.subject_uri == SEED
    assert node.via_uri == repost_uri("alice")


def test_server_time_is_used_not_the_client_one():
    note = like("bob", 5)
    note["record"]["createdAt"] = "1999-01-01T00:00:00.000Z"
    assert parse_notification(note, TRACKED, SEED).created_at == note["indexedAt"]


def test_repost_becomes_a_branch():
    node = parse_notification(repost("alice", 1), TRACKED, SEED)
    assert node.kind == BRANCH
    assert node.via_uri is None


def test_quotes_are_branches_with_or_without_media():
    for note in (quote("q", 1), quote("q", 2, with_media=True)):
        node = parse_notification(note, TRACKED, SEED)
        assert node.kind == BRANCH
        assert node.subject_uri == SEED


def test_reply_in_the_seed_thread_is_a_flower():
    node = parse_notification(reply("carol", 3), TRACKED, SEED)
    assert node.kind == FLOWER
    assert node.subject_uri == ""


def test_things_about_other_posts_are_ignored():
    other = "at://did:plc:bot/app.bsky.feed.post/other"
    assert parse_notification(like("bob", 1, subject=other), TRACKED, SEED) is None
    assert parse_notification(repost("bob", 1, subject=other), TRACKED, SEED) is None
    assert parse_notification(quote("bob", 1, subject=other), TRACKED, SEED) is None
    assert parse_notification(reply("bob", 1, root=other), TRACKED, SEED) is None


def test_other_reasons_are_ignored():
    note = like("bob", 1)
    note["reason"] = "follow"
    assert parse_notification(note, TRACKED, SEED) is None
