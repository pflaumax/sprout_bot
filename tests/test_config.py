import pytest

from sprout.config import Config

KEYS = ["BSKY_HANDLE", "BSKY_APP_PASSWORD", "SPROUT_BATCH", "SPROUT_DATA"]


def test_dotenv_values_comments_and_overrides(tmp_path, monkeypatch):
    for key in KEYS:
        # Set first so monkeypatch remembers to remove what load_dotenv adds.
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    (tmp_path / ".env").write_text(
        "# a comment\n"
        "BSKY_HANDLE=tree.bsky.social\n"
        "BSKY_APP_PASSWORD='abcd-efgh # not a comment'\n"
        "SPROUT_BATCH=25          # post less often\n"
    )
    monkeypatch.setenv("SPROUT_DATA", str(tmp_path / "elsewhere"))
    cfg = Config.load(tmp_path)
    assert cfg.handle == "tree.bsky.social"
    assert cfg.app_password == "abcd-efgh # not a comment"
    assert cfg.batch == 25
    assert cfg.data_dir == tmp_path / "elsewhere"
    assert cfg.data_dir.is_dir()
    assert cfg.min_gap_minutes == 10


def test_bad_settings_stop_the_start(tmp_path, monkeypatch):
    for value in ("0", "abc"):
        monkeypatch.setenv("SPROUT_POLL_SECONDS", value)
        with pytest.raises(SystemExit, match="Bad setting"):
            Config.load(tmp_path)
    monkeypatch.setenv("SPROUT_POLL_SECONDS", "30")
    monkeypatch.setenv("SPROUT_MAX_WAIT_MINUTES", "1")
    with pytest.raises(SystemExit, match="MAX_WAIT"):
        Config.load(tmp_path)
