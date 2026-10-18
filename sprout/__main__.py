"""python -m sprout <command>; run with --help for the list."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from .config import Config

ROOT = Path(__file__).resolve().parent.parent


def _bot(cfg: Config):
    # Imported here so `simulate` works without Bluesky credentials or network.
    from .bot import Bot
    from .bsky import Bluesky
    from .db import Store

    if not cfg.handle or not cfg.app_password:
        sys.exit("Set BSKY_HANDLE and BSKY_APP_PASSWORD in .env (see .env.example).")
    return Bot(
        cfg, Store(cfg.db_path), Bluesky(cfg.handle, cfg.app_password, cfg.session_path)
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m sprout", description="A tree that grows from likes and reposts."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser(
        "seed", help="post a new seed and start growing a tree on it"
    )
    seed.add_argument(
        "--text", help="the seed post's text (default: the one in texts.py)"
    )
    adopt = commands.add_parser(
        "adopt", help="grow a tree on a post that already exists"
    )
    adopt.add_argument("link", help="a bsky.app post link or an at:// URI")
    commands.add_parser(
        "run", help="watch notifications and post updates, until stopped"
    )
    commands.add_parser(
        "preview",
        help="read notifications, save what the next update would be, post nothing",
    )
    sim = commands.add_parser("simulate", help="draw made-up trees, no Bluesky needed")
    sim.add_argument("--events", type=int, default=3000)
    sim.add_argument(
        "--seed", type=int, default=1, help="random seed for the made-up spread"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cfg = Config.load(ROOT)

    if args.command == "simulate":
        from .render import encode, extension, render
        from .simulate import simulate
        from .tree import build_tree

        nodes = simulate(args.events, seed=args.seed)
        cfg.out_dir.mkdir(parents=True, exist_ok=True)
        for fraction in (0.0, 0.01, 0.05, 0.2, 0.5, 1.0):
            n = round(len(nodes) * fraction)
            data = encode(render(build_tree(nodes[:n])).image)
            path = cfg.out_dir / f"sim-{args.seed}-{n:06d}.{extension(data)}"
            path.write_bytes(data)
            print(path)
        return

    bot = _bot(cfg)
    if args.command == "seed":
        tree = bot.plant(args.text) if args.text else bot.plant()
        print(f"Planted: {tree.seed_uri}")
    elif args.command == "adopt":
        tree = bot.adopt(args.link)
        print(f"Growing on: {tree.seed_uri}")
    elif args.command == "preview":
        path, text = bot.preview()
        print(f"{path}\n\n{text}")
    elif args.command == "run":
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        logging.getLogger("sprout").info("growing as @%s", bot.sky.handle)
        bot.run(stop)


if __name__ == "__main__":
    main()
