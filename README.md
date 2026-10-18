# Sprout

A Bluesky bot with one post that grows into a tree.

The bot never speaks first. It plants a seed post and then only reacts:

- **like** → a leaf
- **repost** or **quote** → a new branch
- **reply** in the thread → a flower

Likes that come in **through someone's repost** grow on that person's branch, and
reposts of reposts grow branches on branches. So the tree's shape is a map of
how the post actually travelled. Every so often the bot replies in the seed's
thread with a fresh picture of the tree, and puts the tree on its avatar.

![How a tree grows, from a seedling to 3,000 events (a simulated spread)](docs/growth.png)

*The pictures above come from `python -m sprout simulate`, a made-up spread, not real people.*

## How it works

- **The chain is real.** Since mid-2025, a Bluesky like or repost made from
  someone's repost records which repost it came `via`. The bot reads that from its
  notifications and hangs the leaf on that branch. `via` is set by the client, so
  some apps leave it out; those likes grow on the trunk instead.
- **Posts can't be edited on Bluesky**, so each update is a reply under the
  previous one: the thread is the chain. Likes and reposts of the updates count
  too, because every update is a new chance to spread.
- **It batches.** An update goes out after `SPROUT_BATCH` new things, but no
  sooner than `SPROUT_MIN_GAP_MINUTES` after the last post. A smaller batch goes
  out once its oldest like has waited `SPROUT_MAX_WAIT_MINUTES`, so even after a
  quiet day a lone like waits a while for company. Crossing 10/25/50/100/…
  leaves, or a new longest repost chain, also earns a post. However viral it
  gets, it stays far inside Bluesky's rate limits (5,000 points an hour, 3 per
  post).
- **One leaf per person per post.** Liking, unliking and liking again makes a new
  record each time; it still counts once. One flower per person. A second
  repost (or a quote after a repost) of the same post is the same branch, and
  likes that came through either one grow on it.
- **Leader.** When someone's branch becomes the biggest (counting leaves on all of
  its sub-branches), the update tags them. Each person is tagged at most once per
  tree, however often the lead changes hands. Set `SPROUT_MENTION_LEADER=0` to
  turn that off.
- **Deterministic drawing.** Every random choice is seeded from the record it
  belongs to, so a new like adds a leaf rather than reshuffling the crown.

Unlikes and deleted reposts are not tracked: Bluesky does not notify about them,
so a leaf, once grown, stays.

## Setup

On the Pi (Python 3.11+):

```sh
cd ~/personal/sprout
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # then fill in the handle and an app password
```

Use a separate account for the bot, and an **app password**, never the main one
(Bluesky: Settings → Privacy and security → App passwords). In the profile, say
it is a bot, and mark it with Bluesky's bot label if your app offers it.

## Commands

```sh
.venv/bin/python -m sprout simulate            # draw made-up trees into data/out/, no account needed
.venv/bin/python -m sprout seed                # post the seed (text in sprout/texts.py)
.venv/bin/python -m sprout adopt <link>        # or grow on a post you wrote by hand
.venv/bin/python -m sprout preview             # read notifications, save the next update to data/out/, post nothing
.venv/bin/python -m sprout run                 # the bot itself, until stopped
```

A good first run: `seed`, pin that post in the bot's profile, like it from your
own account, then `preview` to check that the like arrived and how the picture
looks. Then start `run`.

Running `seed` again starts a new tree; the old one stops growing. To go back
to an older tree, `adopt` its seed: it carries on where it stopped. `adopt` only
takes the bot's own posts that start a thread, since Bluesky only tells the bot
about those.

## Running as a service

`deploy/sprout-bot.service` is a systemd unit, set up for user `reiberry` and
`~/personal/sprout`. Copy it to `/etc/systemd/system/sprout-bot.service` (change
`User` and the two paths if yours differ) and, once `seed` or `adopt` has made a
tree:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now sprout-bot
journalctl -u sprout-bot -f
```

Everything the bot keeps is in `data/` (`SPROUT_DATA`): `sprout.db` (SQLite),
`session.txt` (the login session, readable only by its owner, reused across
restarts because Bluesky limits logins) and `out/`.

`scripts/backup-db.sh` copies `sprout.db` through SQLite's backup API (safe while
the bot writes) into `~/sprout_backups` and `/mnt/hdd/sprout_backups`, keeping
the last 14. Run it daily from cron; the line is at the top of the script. The
session needs no backup: losing it only means one fresh login.

When Bluesky rejects the session, or ten steps in a row fail, the bot exits with
code 1 and systemd starts it again with a fresh login. A wrong handle or app
password exits with 78, which the unit file tells systemd not to retry: fix
`.env` and `sudo systemctl restart sprout-bot`. If the Pi was off, the bot reads back
through up to 10,000 notifications to where it stopped.

## Settings

All optional, in `.env` or the environment. See `.env.example`. A value that
makes no sense (a poll under 5 s, a max wait below the min gap) stops the bot
at start with a message saying which.

| Variable | Default | |
|---|---|---|
| `SPROUT_POLL_SECONDS` | 30 | how often to read notifications |
| `SPROUT_BATCH` | 10 | new things before an update |
| `SPROUT_MIN_GAP_MINUTES` | 10 | minimum time between updates |
| `SPROUT_MAX_WAIT_MINUTES` | 60 | longest a smaller batch waits |
| `SPROUT_UPDATE_AVATAR` | 1 | put the tree on the bot's avatar |
| `SPROUT_MENTION_LEADER` | 1 | tag the new biggest branch |
| `SPROUT_DATA` | `./data` | where state lives |

What the bot says is all in `sprout/texts.py`, with Ukrainian plural forms.

## Layout

| File | |
|---|---|
| `sprout/events.py` | a notification → a leaf, branch or flower |
| `sprout/tree.py` | who hangs off whom; stats and the leader |
| `sprout/render.py` | the picture (Pillow) and fitting it under the 1 MB upload limit |
| `sprout/bot.py` | the loop: poll, decide, post |
| `sprout/bsky.py` | the only module that talks to Bluesky (the `atproto` SDK) |
| `sprout/errors.py` | reading the SDK's errors without importing it |
| `sprout/db.py` | SQLite storage |
| `sprout/texts.py` | every word the bot says |
| `sprout/simulate.py` | made-up spreads for trying the drawing |

## Tests

```sh
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

The tests run the whole bot against a fake Bluesky, and check the SDK seam
against the SDK's own model classes. Nothing in them touches the network.
