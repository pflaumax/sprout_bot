"""Settings, from the environment or from a .env file in the project folder.
Real environment variables win over the file, so systemd can override it."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in ("'", '"'):
            value = value[1:].split(value[0], 1)[0]
        else:
            value = value.split(" #", 1)[0].strip()  # a trailing comment
        os.environ.setdefault(key.strip(), value)


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    handle: str
    app_password: str
    data_dir: Path
    poll_seconds: int  # how often to read notifications
    # Post an update after `batch` new leaves, branches and flowers, but never
    # sooner than `min_gap_minutes` after the last one, and never keep a smaller
    # batch waiting longer than `max_wait_minutes`.
    batch: int
    min_gap_minutes: float
    max_wait_minutes: float
    update_avatar: bool
    mention_leader: bool  # tag whoever's branch has just become the biggest

    def __post_init__(self) -> None:
        problems = []
        if self.poll_seconds < 5:
            problems.append("SPROUT_POLL_SECONDS must be at least 5")
        if self.batch < 1:
            problems.append("SPROUT_BATCH must be at least 1")
        if self.min_gap_minutes < 0:
            problems.append("SPROUT_MIN_GAP_MINUTES cannot be negative")
        if self.max_wait_minutes < self.min_gap_minutes:
            problems.append("SPROUT_MAX_WAIT_MINUTES cannot be below the min gap")
        if problems:
            raise ValueError("; ".join(problems))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sprout.db"

    @property
    def session_path(self) -> Path:
        return self.data_dir / "session.txt"

    @property
    def out_dir(self) -> Path:
        return self.data_dir / "out"

    @classmethod
    def load(cls, root: Path) -> Config:
        load_dotenv(root / ".env")
        env = os.environ.get
        data_dir = Path(env("SPROUT_DATA", str(root / "data")))
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            return cls._from(env, data_dir)
        except ValueError as e:  # a bad number, or a check above
            raise SystemExit(f"Bad setting: {e}") from e

    @classmethod
    def _from(cls, env, data_dir: Path) -> Config:
        return cls(
            handle=env("BSKY_HANDLE", ""),
            app_password=env("BSKY_APP_PASSWORD", ""),
            data_dir=data_dir,
            poll_seconds=int(env("SPROUT_POLL_SECONDS", "30")),
            batch=int(env("SPROUT_BATCH", "10")),
            min_gap_minutes=float(env("SPROUT_MIN_GAP_MINUTES", "10")),
            max_wait_minutes=float(env("SPROUT_MAX_WAIT_MINUTES", "60")),
            update_avatar=_flag("SPROUT_UPDATE_AVATAR", "1"),
            mention_leader=_flag("SPROUT_MENTION_LEADER", "1"),
        )
