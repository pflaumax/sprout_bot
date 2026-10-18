import io

from PIL import Image, ImageChops

from sprout.events import BRANCH, LEAF, Node
from sprout.render import (
    UPLOAD_LIMIT,
    _bundles,
    _layout,
    _Seg,
    _sprigs,
    _weights,
    encode,
    extension,
    render,
)
from sprout.simulate import simulate
from sprout.tree import Branch, build_tree


def test_seedling_and_big_tree_render_and_fit_the_upload_limit():
    for nodes in ([], simulate(3000, seed=2)):
        rendered = render(build_tree(nodes))
        assert rendered.image.size == (1600, 1600)
        data = encode(rendered.image)
        assert len(data) <= UPLOAD_LIMIT
        assert Image.open(io.BytesIO(data)).size == (1600, 1600)
        assert rendered.avatar(800).size == (800, 800)


def test_same_tree_draws_the_same_picture():
    nodes = simulate(300, seed=4)
    first = render(build_tree(nodes)).image.tobytes()
    assert render(build_tree(list(reversed(nodes)))).image.tobytes() == first


def test_encode_falls_back_to_jpeg_when_png_is_too_big():
    noise = Image.effect_noise((800, 800), 100).convert("RGB")
    data = encode(noise, limit=400_000)
    assert data[:2] == b"\xff\xd8"
    assert len(data) <= 400_000


def direct_likes(n):
    return [
        Node(
            f"at://did:plc:u{i}/app.bsky.feed.like/{i}",
            LEAF,
            f"did:plc:u{i}",
            f"u{i}.test",
            "at://seed",
            None,
            f"2026-10-17T12:{i // 60:02d}:{i % 60:02d}.000Z",
        )
        for i in range(n)
    ]


def changed_pixels(a, b):
    diff = ImageChops.difference(a, b).convert("L").point(lambda p: 255 if p > 8 else 0)
    return diff.histogram()[255]


def test_a_new_like_never_regroups_the_ones_already_drawn():
    for n in range(1, 60):
        before, after = _bundles(list(range(n))), _bundles(list(range(n + 1)))
        assert after[: len(before) - 1] == before[:-1]
        assert after[len(before) - 1][: len(before[-1])] == before[-1]


def test_one_more_like_changes_the_picture_evenly_even_at_a_new_twig():
    # 48 -> 49 starts a new twig (and 42 -> 43 was where the old grouping
    # reshuffled every twig); neither should cost more than any other like.
    pictures = {
        n: render(build_tree(direct_likes(n)), 400).image for n in range(40, 52)
    }
    steps = [changed_pixels(pictures[n], pictures[n + 1]) for n in range(40, 51)]
    assert max(steps) < 2 * min(steps)


def test_a_later_repost_does_not_move_older_twigs():
    nodes = direct_likes(30)
    trunk = build_tree(nodes)
    segs = _layout(trunk, _weights(trunk), 0.6, 0.05)
    firsts = [
        (b[0][0].created_at, b[0][0].uri) for b in _bundles([(x,) for x in nodes])
    ]
    later = Branch(
        node=Node(
            "at://r",
            BRANCH,
            "did:plc:late",
            "late",
            "s",
            None,
            "2026-10-18T00:00:00.000Z",
        ),
        parent=trunk,
        depth=1,
    )
    grown = segs + [_Seg(later, (0, 0.5), (0.2, 0.7), (0.4, 0.9), 0.01)]
    assert [t.p0 for t in _sprigs(grown, 0.6, firsts)] == [
        t.p0 for t in _sprigs(segs, 0.6, firsts)
    ]


def test_extension_names_what_encode_made():
    assert extension(encode(render(build_tree([])).image)) == "png"
    noise = Image.effect_noise((800, 800), 100).convert("RGB")
    assert extension(encode(noise, limit=400_000)) == "jpg"
