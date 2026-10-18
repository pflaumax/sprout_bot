"""The seam with the atproto SDK, checked against the SDK's own model classes
with the network calls stubbed out."""

import stat
from types import SimpleNamespace

import pytest
from atproto import SessionEvent, models
from atproto_client.exceptions import (
    BadRequestError,
    InvokeTimeoutError,
    UnauthorizedError,
)
from atproto_client.models.common import XrpcError
from atproto_client.models.utils import get_model_as_dict, get_or_create
from helpers import SEED, like, repost_uri, stamp

from sprout import bsky
from sprout.bsky import CATCH_UP_PAGES, FIRST_RUN_PAGES, Bluesky
from sprout.events import LEAF, parse_notification

BLOB = {
    "$type": "blob",
    "ref": {"$link": "bafkreihdwdcefgh4dqkjv67uzcmw7ojee6xedzdetojuzjevtenxquvyku"},
    "mimeType": "image/png",
    "size": 1000,
}


def offline(client) -> Bluesky:
    sky = Bluesky.__new__(Bluesky)
    sky.client, sky.did, sky.handle = client, "did:plc:bot", "sprout.test"
    return sky


def sdk_notification(note: dict):
    return models.AppBskyNotificationListNotifications.Notification(
        uri=note["uri"],
        cid=note["cid"],
        reason=note["reason"],
        indexed_at=note["indexedAt"],
        is_read=False,
        author=models.AppBskyActorDefs.ProfileView(**note["author"]),
        record=get_or_create(note["record"], strict=False),
    )


def test_notifications_come_back_as_lexicon_json_oldest_first():
    pages = [
        ([like("b", 2, via=repost_uri("alice")), like("a", 1)], "next"),
        ([like("z", 0)], None),
    ]
    calls = []

    def list_notifications(params):
        calls.append(params)
        notes, cursor = pages[len(calls) - 1]
        return SimpleNamespace(
            notifications=[sdk_notification(n) for n in notes], cursor=cursor
        )

    client = SimpleNamespace(
        app=SimpleNamespace(
            bsky=SimpleNamespace(
                notification=SimpleNamespace(list_notifications=list_notifications)
            )
        )
    )
    found = offline(client).notifications(None)
    assert [n["author"]["handle"] for n in found] == ["z.test", "a.test", "b.test"]
    assert calls[0]["reasons"] == ["like", "repost", "reply", "quote"]
    assert calls[1]["cursor"] == "next"

    node = parse_notification(found[-1], {SEED}, SEED)
    assert node.kind == LEAF
    assert node.via_uri == repost_uri("alice")


def test_paging_stops_once_it_reaches_what_was_seen():
    calls = []

    def list_notifications(params):
        calls.append(params)
        return SimpleNamespace(
            notifications=[sdk_notification(like("a", 1))], cursor="more"
        )

    client = SimpleNamespace(
        app=SimpleNamespace(
            bsky=SimpleNamespace(
                notification=SimpleNamespace(list_notifications=list_notifications)
            )
        )
    )
    offline(client).notifications("2026-10-17T12:00:05.000Z")
    assert len(calls) == 1


def test_post_carries_the_mention_facet_and_the_thread():
    sent = {}
    client = SimpleNamespace(
        send_image=lambda **kw: (
            sent.update(kw) or SimpleNamespace(uri="at://p", cid="c")
        )
    )
    ref = offline(client).post(
        [("text", "Найбільша гілка у "), ("mention", "@alice.test", "did:plc:alice")],
        b"png",
        "alt",
        (1600, 1600),
        reply_to=((SEED, "c0"), ("at://parent", "c1")),
    )
    assert ref == ("at://p", "c")
    facet = get_model_as_dict(sent["text"].build_facets()[0])
    start = len("Найбільша гілка у ".encode())
    assert facet["index"]["byteStart"] == start
    assert facet["index"]["byteEnd"] == start + len(b"@alice.test")
    assert facet["features"][0]["did"] == "did:plc:alice"
    assert sent["reply_to"].root.uri == SEED
    assert sent["reply_to"].parent.uri == "at://parent"
    assert sent["langs"] == ["uk"]
    assert (sent["image_aspect_ratio"].width, sent["image_aspect_ratio"].height) == (
        1600,
        1600,
    )


def test_avatar_change_keeps_the_rest_of_the_profile():
    put = {}
    profile = get_or_create(
        {
            "$type": "app.bsky.actor.profile",
            "displayName": "Паросток",
            "description": "бот",
            "labels": {
                "$type": "com.atproto.label.defs#selfLabels",
                "values": [{"val": "bot"}],
            },
        },
        strict=False,
    )
    client = SimpleNamespace(
        upload_blob=lambda data: (
            models.ComAtprotoRepoUploadBlob.Response.model_validate({"blob": BLOB})
        ),
        com=SimpleNamespace(
            atproto=SimpleNamespace(
                repo=SimpleNamespace(
                    get_record=lambda params: SimpleNamespace(
                        value=profile, cid="bafyprofile"
                    ),
                    put_record=lambda data: put.update(data),
                )
            )
        ),
    )
    offline(client).set_avatar(b"png")
    record = put["record"]
    assert record["displayName"] == "Паросток"
    assert record["labels"]["values"][0]["val"] == "bot"
    assert record["avatar"]["ref"] == BLOB["ref"]
    assert put["swapRecord"] == "bafyprofile"


def profile_client(get_record, put):
    return SimpleNamespace(
        upload_blob=lambda data: (
            models.ComAtprotoRepoUploadBlob.Response.model_validate({"blob": BLOB})
        ),
        com=SimpleNamespace(
            atproto=SimpleNamespace(
                repo=SimpleNamespace(
                    get_record=get_record, put_record=lambda data: put.update(data)
                )
            )
        ),
    )


def test_a_failed_profile_read_never_overwrites_the_profile():
    put = {}

    def timeout(params):
        raise InvokeTimeoutError()

    with pytest.raises(InvokeTimeoutError):
        offline(profile_client(timeout, put)).set_avatar(b"png")
    assert put == {}


def test_a_missing_profile_starts_from_an_empty_one():
    put = {}

    def missing(params):
        raise BadRequestError(
            SimpleNamespace(
                status_code=400,
                content=XrpcError(error="RecordNotFound", message=None),
                headers={},
            )
        )

    offline(profile_client(missing, put)).set_avatar(b"png")
    assert put["record"]["avatar"]["ref"] == BLOB["ref"]
    assert "swapRecord" not in put


def paging_client(pages: int, calls: list):
    def list_notifications(params):
        calls.append(params)
        n = len(calls)
        note = like(f"u{n}", 10_000 - n)  # each page older than the last
        return SimpleNamespace(notifications=[sdk_notification(note)], cursor="more")

    return SimpleNamespace(
        app=SimpleNamespace(
            bsky=SimpleNamespace(
                notification=SimpleNamespace(list_notifications=list_notifications)
            )
        )
    )


def test_catching_up_reads_back_to_where_it_stopped(caplog):
    calls = []
    found = offline(paging_client(0, calls)).notifications(stamp(10_000 - 50))
    assert len(calls) == 51  # past the old 10-page cap, stopping once it got there
    assert found[0]["indexedAt"] < found[-1]["indexedAt"]
    assert "skipped" not in caplog.text


def test_a_gap_too_big_to_read_back_is_logged(caplog):
    calls = []
    offline(paging_client(0, calls)).notifications(stamp(0))
    assert len(calls) == CATCH_UP_PAGES
    assert "older ones are skipped" in caplog.text


def test_the_first_run_reads_only_recent_ones():
    calls = []
    offline(paging_client(0, calls)).notifications(None)
    assert len(calls) == FIRST_RUN_PAGES


class StubClient:
    """Stands in for atproto.Client in Bluesky.__init__."""

    accounts = {"session-of-old": ("old.bsky.social", "did:plc:old")}
    password_logins: list = []
    refuse_password = False

    def __init__(self):
        self.me = None

    def on_session_change(self, callback):
        pass

    def login(self, login=None, password=None, session_string=None):
        if session_string is not None:
            if session_string not in self.accounts:
                raise ValueError("bad session")
            handle, did = self.accounts[session_string]
        else:
            StubClient.password_logins.append(login)
            if StubClient.refuse_password:
                raise UnauthorizedError(
                    SimpleNamespace(
                        status_code=401,
                        content=XrpcError(error="AuthenticationRequired", message=None),
                        headers={},
                    )
                )
            handle, did = login, "did:plc:" + login.split(".")[0]
        self.me = SimpleNamespace(handle=handle, did=did)


@pytest.fixture
def stub(monkeypatch):
    monkeypatch.setattr(bsky, "Client", StubClient)
    StubClient.password_logins = []
    StubClient.refuse_password = False


def test_a_saved_session_for_another_account_is_not_used(tmp_path, stub):
    session = tmp_path / "session.txt"
    session.write_text("session-of-old")
    sky = Bluesky("new.bsky.social", "pw", session)
    assert sky.handle == "new.bsky.social"
    assert StubClient.password_logins == ["new.bsky.social"]


def test_a_saved_session_for_this_account_is_reused(tmp_path, stub):
    session = tmp_path / "session.txt"
    session.write_text("session-of-old")
    Bluesky("@Old.bsky.social", "pw", session)
    assert StubClient.password_logins == []


def test_a_corrupt_session_falls_back_to_the_password(tmp_path, stub):
    session = tmp_path / "session.txt"
    session.write_text("garbage-cut-short")
    Bluesky("new.bsky.social", "pw", session)
    assert StubClient.password_logins == ["new.bsky.social"]


def test_a_wrong_password_exits_with_78_so_systemd_stops_retrying(tmp_path, stub):
    StubClient.refuse_password = True
    with pytest.raises(SystemExit) as exit:
        Bluesky("new.bsky.social", "wrong", tmp_path / "session.txt")
    assert exit.value.code == 78


def test_the_session_file_is_private_and_written_whole(tmp_path):
    sky = offline(None)
    sky.session_file = tmp_path / "session.txt"
    sky._keep_session(SessionEvent.CREATE, SimpleNamespace(encode=lambda: "secret"))
    assert sky.session_file.read_text() == "secret"
    assert stat.S_IMODE(sky.session_file.stat().st_mode) == 0o600
    assert not (tmp_path / "session.tmp").exists()
