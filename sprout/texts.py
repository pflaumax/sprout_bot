"""Everything the bot says, in one place, so the wording can change without
touching the logic.

Post text is returned as segments rather than a string: ("text", s) or
("mention", "@handle", did). The client turns a mention into a facet, which is
what makes Bluesky link it and notify that person.
"""

from __future__ import annotations

from .tree import Stats

MAX_CHARS = 290  # Bluesky allows 300 graphemes; leave room for emoji counting

SEED = (
    "Я насінина, і з мене може вирости дерево.\n"
    "\n"
    "Лайкни, і на ньому з'явиться листок. Зроби репост, і проросте нова гілка."
    " Відповідай тут, і розквітне квітка.\n"
    "\n"
    "Якщо хтось лайкне мене у твоєму репості, цей листок виросте на твоїй гілці.\n"
    "\n"
    "Час від часу я показуватиму, яким стало дерево."
)


def plural(n: int, one: str, few: str, many: str) -> str:
    """Ukrainian counting form: 1 листок, 2 листки, 5 листків, 21 листок."""
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{n} {word}"


def leaves(n: int) -> str:
    return plural(n, "листок", "листки", "листків")


def branches(n: int) -> str:
    return plural(n, "гілка", "гілки", "гілок")


def flowers(n: int) -> str:
    return plural(n, "квітка", "квітки", "квіток")


def update(now: Stats, before: Stats, tag_leader: bool) -> list[tuple]:
    """`tag_leader` says whether a new biggest branch may be tagged; the bot
    turns it off for people it has tagged before."""
    grown = [
        f"+{f(d)}"
        for f, d in (
            (leaves, now.leaves - before.leaves),
            (branches, now.branches - before.branches),
            (flowers, now.flowers - before.flowers),
        )
        if d > 0
    ]
    lines = [
        "🌳 " + (" · ".join(grown) if grown else "дерево трохи підросло"),
        "",
        f"Разом: {leaves(now.leaves)}, {branches(now.branches)},"
        f" {flowers(now.flowers)}.",
    ]
    if now.depth:
        record = ". Це новий рекорд!" if now.depth > before.depth else ""
        lines.append(f"Найдовший ланцюг репостів: {now.depth}{record}")

    segments: list[tuple] = [("text", "\n".join(lines))]
    new_leader = now.leader_did and now.leader_did != before.leader_did
    if tag_leader and new_leader and now.leader_handle:
        line = [
            ("text", "\nНайбільша гілка тепер у "),
            ("mention", "@" + now.leader_handle, now.leader_did),
            ("text", f", на ній {leaves(now.leader_leaves)}."),
        ]
        if length(segments) + length(line) <= MAX_CHARS:
            segments += line
    return segments


def length(segments: list[tuple]) -> int:
    return sum(len(s[1]) for s in segments)


def plain(segments: list[tuple]) -> str:
    return "".join(s[1] for s in segments)


def mentioned(segments: list[tuple]) -> list[str]:
    return [s[2] for s in segments if s[0] == "mention"]


def alt(stats: Stats) -> str:
    if stats.total == 0:
        return "Маленький паросток із двома листочками. Дерево ще не почало рости."
    return (
        f"Згенероване дерево: {leaves(stats.leaves)}, {branches(stats.branches)}, "
        f"{flowers(stats.flowers)}. Гілки виросли з репостів, листки з лайків, "
        "квіти з відповідей. Лайки, що прийшли через чийсь репост, ростуть на "
        "його гілці."
    )
