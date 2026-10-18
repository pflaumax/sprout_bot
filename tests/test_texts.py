from sprout import texts
from sprout.tree import Stats


def test_ukrainian_plurals():
    forms = {
        n: texts.leaves(n)
        for n in (0, 1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 101, 111, 112)
    }
    assert forms == {
        0: "0 листків",
        1: "1 листок",
        2: "2 листки",
        4: "4 листки",
        5: "5 листків",
        11: "11 листків",
        12: "12 листків",
        14: "14 листків",
        21: "21 листок",
        22: "22 листки",
        25: "25 листків",
        101: "101 листок",
        111: "111 листків",
        112: "112 листків",
    }
    assert texts.branches(3) == "3 гілки"
    assert texts.flowers(1) == "1 квітка"


def test_update_lists_only_what_grew():
    before = Stats(leaves=10, branches=2, flowers=1, depth=1)
    now = Stats(leaves=13, branches=2, flowers=2, depth=1)
    text = texts.plain(texts.update(now, before, tag_leader=True))
    assert text.startswith("🌳 +3 листки · +1 квітка\n")
    assert "Разом: 13 листків, 2 гілки, 2 квітки." in text
    assert "рекорд" not in text


def test_a_longer_chain_is_called_out():
    text = texts.plain(
        texts.update(
            Stats(leaves=1, branches=3, depth=3), Stats(branches=2, depth=2), False
        )
    )
    assert "Найдовший ланцюг репостів: 3. Це новий рекорд!" in text


def test_leader_is_mentioned_only_when_it_changes():
    now = Stats(
        leaves=40,
        branches=5,
        leader_did="did:plc:alice",
        leader_handle="alice.test",
        leader_leaves=37,
    )
    segments = texts.update(now, Stats(leader_did="did:plc:bob"), tag_leader=True)
    assert ("mention", "@alice.test", "did:plc:alice") in segments
    assert texts.plain(segments).endswith(
        "Найбільша гілка тепер у @alice.test, на ній 37 листків."
    )

    assert all(s[0] == "text" for s in texts.update(now, now, tag_leader=True))
    assert all(s[0] == "text" for s in texts.update(now, Stats(), tag_leader=False))


def test_update_fits_in_a_post():
    huge = Stats(
        leaves=1_234_567,
        branches=123_456,
        flowers=12_345,
        depth=99,
        leader_did="did:plc:x",
        leader_handle="a-very-long-handle-for-someone.bsky.social",
        leader_leaves=999_999,
    )
    assert texts.length(texts.update(huge, Stats(), tag_leader=True)) <= 300
    assert len(texts.SEED) <= 300


def test_no_dashes_anywhere():
    full = Stats(
        leaves=40,
        branches=5,
        flowers=2,
        depth=3,
        leader_did="did:plc:alice",
        leader_handle="alice.test",
        leader_leaves=37,
    )
    update = texts.plain(texts.update(full, Stats(depth=2), tag_leader=True))
    for text in (texts.SEED, update, texts.alt(full), texts.alt(Stats())):
        assert "—" not in text and "–" not in text
