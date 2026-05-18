#!/usr/bin/env python3
from __future__ import annotations
"""Instagram Agent -- Post stories, feed posts, and reels with tagging support."""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = AGENT_DIR / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
STATUS_PATH = AGENT_DIR / "status.json"
CONFIG_DIR = Path.home() / ".instagram-agent"
DEVICE_PATH = CONFIG_DIR / "device.json"
USAGE_PATH = DATA_DIR / "usage.json"

DAILY_CAPS = {
    "sent_dms": 20,
}

from dotenv import load_dotenv


def load_environment():
    candidates = []

    explicit_env = os.environ.get("INSTAGRAM_AGENT_ENV")
    if explicit_env:
        candidates.append(Path(explicit_env).expanduser())

    candidates.extend([
        AGENT_DIR / ".env",
        Path.home() / ".instagram-agent" / ".env",
    ])

    for env_path in candidates:
        if env_path.exists():
            load_dotenv(env_path)
            return env_path

    return None


ENV_PATH = load_environment()

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

IG_USERNAME = os.environ.get("IG_USERNAME", "")


# ── Human timing ──────────────────────────────────────────────────
# Named delay profiles that mimic realistic human interaction pacing.
# Err on the side of longer — safety matters more than speed.

_PAUSE_RANGES = {
    "glance":  (1.5,  4.0),   # brief moment before a quick action
    "read":    (3.0,  8.0),   # reading a message or scanning a thread
    "scroll":  (5.0, 14.0),   # browsing through a list (inbox, comments)
    "think":   (6.0, 16.0),   # pausing before looking at a profile
    "compose": (4.0, 10.0),   # opening a compose window
    "post":    (10.0, 28.0),  # reviewing content before hitting Post
}


def human_pause(kind: str = "glance") -> None:
    lo, hi = _PAUSE_RANGES.get(kind, _PAUSE_RANGES["glance"])
    duration = random.uniform(lo, hi)
    print(f"  [human pause: {duration:.1f}s]", file=sys.stderr, flush=True)
    time.sleep(duration)


def typing_delay(text: str) -> None:
    """Pause proportional to typing ~40 WPM with human variance."""
    words = max(1, len(text.split()))
    base_seconds = (words / 40) * 60
    jitter = random.uniform(0.75, 1.5)
    duration = max(3.0, base_seconds * jitter)
    print(f"  [typing delay: {duration:.1f}s for {words} words]", file=sys.stderr, flush=True)
    time.sleep(duration)


# ── Helpers ───────────────────────────────────────────────────────

def write_status(status: str, result: str | None, message: str | None):
    data = {
        "agentId": "instagram",
        "status": status,
        "lastRun": datetime.now(timezone.utc).isoformat(),
        "lastResult": result,
        "lastMessage": message,
        "errorCount": 0,
        "enabled": True,
    }
    try:
        existing = json.loads(STATUS_PATH.read_text())
        if result == "error":
            data["errorCount"] = existing.get("errorCount", 0) + 1
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    STATUS_PATH.write_text(json.dumps(data, indent=2) + "\n")


def get_or_create_device() -> dict:
    """Load or generate a persistent device fingerprint stored in ~/.instagram-agent/device.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if DEVICE_PATH.exists():
        try:
            return json.loads(DEVICE_PATH.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    from instagrapi import Client
    tmp = Client()
    device = {
        "device_settings": tmp.device_settings,
        "user_agent": tmp.user_agent,
    }
    DEVICE_PATH.write_text(json.dumps(device, indent=2) + "\n")
    return device


def _load_usage() -> dict:
    try:
        return json.loads(USAGE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_usage(usage: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    pruned = {k: v for k, v in usage.items() if k >= cutoff}
    USAGE_PATH.write_text(json.dumps(pruned, indent=2) + "\n")


def check_daily_cap(username: str, action: str) -> None:
    cap = DAILY_CAPS.get(action)
    if cap is None:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage = _load_usage()
    count = usage.get(today, {}).get(username, {}).get(action, 0)
    if count >= cap:
        print(
            f"Error: Daily cap reached for {action} ({cap}/day for @{username}). "
            "Resets after midnight UTC.",
            file=sys.stderr,
        )
        sys.exit(1)


def record_action(username: str, action: str) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage = _load_usage()
    usage.setdefault(today, {}).setdefault(username, {})[action] = (
        usage.get(today, {}).get(username, {}).get(action, 0) + 1
    )
    _save_usage(usage)


def get_client(username: str = IG_USERNAME):
    """Return an authenticated instagrapi Client, using saved session if available."""
    if not username:
        print(
            "Error: IG_USERNAME is not set. Add it to ~/.instagram-agent/.env",
            file=sys.stderr,
        )
        sys.exit(1)

    from instagrapi import Client
    from instagrapi.exceptions import (
        BadPassword,
        ChallengeRequired,
        FeedbackRequired,
        LoginRequired,
        RateLimitError,
    )

    device = get_or_create_device()
    cl = Client()
    cl.set_device(device["device_settings"])
    cl.set_user_agent(device["user_agent"])
    cl.delay_range = [2, 5]
    cl.request_timeout = 30

    session_path = SESSIONS_DIR / f"{username}.json"
    if session_path.exists():
        try:
            cl.load_settings(session_path)
            cl.account_info()  # cheap verify — avoids a full re-login roundtrip
            cl.dump_settings(session_path)
            return cl
        except ChallengeRequired:
            print(
                "Error: Instagram requires verification. Open the Instagram app, complete "
                "any security prompts, then run `instagram login` to restore the session.",
                file=sys.stderr,
            )
            sys.exit(1)
        except (LoginRequired, Exception):
            pass  # session stale — fall through to password login

    password = os.environ.get("IG_PASSWORD")
    if not password:
        print("Error: IG_PASSWORD not set. Add it to ~/.instagram-agent/.env", file=sys.stderr)
        sys.exit(1)

    try:
        cl.login(username, password)
    except ChallengeRequired:
        print(
            "Error: Instagram requires verification at login. Open the Instagram app, complete "
            "any security prompts, then try again.",
            file=sys.stderr,
        )
        sys.exit(1)
    except BadPassword:
        print("Error: Instagram password is incorrect. Update IG_PASSWORD in your .env file.", file=sys.stderr)
        sys.exit(1)
    except FeedbackRequired as e:
        print(f"Error: Instagram blocked this action. Try again later. Detail: {e}", file=sys.stderr)
        sys.exit(1)
    except RateLimitError:
        print("Error: Instagram rate limit hit. Wait a few minutes before trying again.", file=sys.stderr)
        sys.exit(1)

    cl.dump_settings(session_path)
    return cl


def resolve_media_id(cl, media_ref: str) -> str:
    """Resolve an Instagram media reference to a full media_id."""
    value = (media_ref or "").strip()
    if not value:
        raise ValueError("media reference is required")

    if value.isdigit() and "_" in value:
        return value

    if value.startswith("http://") or value.startswith("https://"):
        media_pk = cl.media_pk_from_url(value)
        return cl.media_id(media_pk)

    normalized = value.rstrip("/").split("/")[-1]
    if normalized.isdigit():
        return cl.media_id(normalized)

    media_pk = cl.media_pk_from_code(normalized)
    return cl.media_id(media_pk)


# ── Commands ──────────────────────────────────────────────────────

def cmd_login(args):
    """Authenticate and save session (handles 2FA)."""
    password = os.environ.get("IG_PASSWORD")
    if not password:
        print("Error: IG_PASSWORD not set in .env", file=sys.stderr)
        sys.exit(1)

    from instagrapi import Client
    from instagrapi.exceptions import TwoFactorRequired

    device = get_or_create_device()
    cl = Client()
    cl.set_device(device["device_settings"])
    cl.set_user_agent(device["user_agent"])
    cl.delay_range = [2, 5]

    session_path = SESSIONS_DIR / f"{args.username}.json"
    print(f"Logging in as @{args.username}...")

    try:
        cl.login(args.username, password)
    except TwoFactorRequired as e:
        print("2FA required. Check your phone or authenticator app for a code.")
        if args.code:
            code = args.code.strip()
        else:
            code = input("Enter 2FA code: ").strip()

        two_factor_info = e.args[0] if e.args and isinstance(e.args[0], dict) else {}
        identifier = two_factor_info.get("two_factor_identifier", "")
        cl.two_factor_login(
            args.username,
            password,
            verification_code=code,
            two_factor_identifier=identifier,
        )

    cl.dump_settings(session_path)
    write_status("idle", "success", f"Logged in as @{args.username}")
    print(f"Session saved to {session_path}")


def cmd_post_story(args):
    """Upload a video (or image) as an Instagram Story with optional user tags."""
    path = Path(args.path).resolve()
    if not path.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    write_status("working", None, f"Posting story: {path.name}")
    try:
        cl = get_client(args.username)

        mentions = []
        if args.tag:
            print(f"Resolving {len(args.tag)} user tag(s)...")
            for handle in args.tag:
                handle = handle.lstrip("@")
                try:
                    user_id = cl.user_id_from_username(handle)
                    mentions.append({"user_id": user_id, "username": handle})
                    print(f"  ✓ @{handle} → {user_id}")
                    human_pause("glance")  # pause between each tag lookup
                except Exception as e:
                    print(f"  ✗ @{handle}: {e}", file=sys.stderr)

        suffix = path.suffix.lower()

        # Build StoryMention objects for resolved users
        story_mentions = []
        if mentions:
            from instagrapi.types import StoryMention, UserShort
            positions = [
                (0.5, 0.8), (0.2, 0.2), (0.8, 0.2), (0.2, 0.8),
                (0.8, 0.8), (0.5, 0.5), (0.3, 0.5), (0.7, 0.5),
                (0.5, 0.3), (0.4, 0.7), (0.6, 0.7), (0.4, 0.3), (0.6, 0.3),
            ]
            for i, m in enumerate(mentions):
                x, y = positions[i % len(positions)]
                story_mentions.append(
                    StoryMention(
                        user=UserShort(pk=int(m["user_id"]), username=m["username"]),
                        x=x, y=y, width=0.5, height=0.06,
                    )
                )

        human_pause("post")  # reviewing story before posting
        if suffix in (".mp4", ".mov", ".avi", ".mkv"):
            print("Uploading video story...")
            media = cl.video_upload_to_story(path, mentions=story_mentions)
        else:
            print("Uploading photo story...")
            media = cl.photo_upload_to_story(path, mentions=story_mentions)

        write_status("idle", "success", f"Story posted: {media.pk}")
        print(f"\nStory posted successfully!")
        print(f"Media ID: {media.pk}")
        if mentions:
            print(f"Tagged: {', '.join('@' + m['username'] for m in mentions)}")

    except Exception as e:
        write_status("idle", "error", str(e))
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_post_feed(args):
    """Upload a video or image as a feed post."""
    path = Path(args.path).resolve()
    if not path.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    write_status("working", None, f"Posting to feed: {path.name}")
    try:
        cl = get_client(args.username)
        caption = args.caption or ""
        suffix = path.suffix.lower()

        human_pause("post")  # reviewing caption and content before posting
        if suffix in (".mp4", ".mov", ".avi", ".mkv"):
            print("Uploading video to feed...")
            media = cl.video_upload(path, caption=caption)
        else:
            print("Uploading photo to feed...")
            media = cl.photo_upload(path, caption=caption)

        write_status("idle", "success", f"Feed post: {media.pk}")
        print(f"\nFeed post published!")
        print(f"Media ID: {media.pk}")

    except Exception as e:
        write_status("idle", "error", str(e))
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_post_reel(args):
    """Upload a video as a Reel."""
    path = Path(args.path).resolve()
    if not path.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    write_status("working", None, f"Posting reel: {path.name}")
    try:
        cl = get_client(args.username)
        caption = args.caption or ""
        human_pause("post")  # reviewing reel and caption before posting
        print("Uploading reel...")
        media = cl.clip_upload(path, caption=caption)
        write_status("idle", "success", f"Reel posted: {media.pk}")
        print(f"\nReel posted!")
        print(f"Media ID: {media.pk}")

    except Exception as e:
        write_status("idle", "error", str(e))
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_post_carousel(args):
    """Upload multiple images as a carousel (album) feed post."""
    paths = [Path(p).resolve() for p in args.paths]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        print(f"Error: files not found: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    if len(paths) < 2:
        print("Error: carousel requires at least 2 images", file=sys.stderr)
        sys.exit(1)

    write_status("working", None, f"Posting carousel: {len(paths)} images")
    try:
        cl = get_client(args.username)
        caption = args.caption or ""
        human_pause("post")  # reviewing carousel images and caption before posting
        print(f"Uploading carousel ({len(paths)} images)...")
        media = cl.album_upload(paths, caption=caption)
        write_status("idle", "success", f"Carousel posted: {media.pk}")
        print(f"\nCarousel posted!")
        print(f"Media ID: {media.pk}")
    except Exception as e:
        write_status("idle", "error", str(e))
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_resolve_user(args):
    """Resolve an Instagram username to a user ID."""
    try:
        cl = get_client(args.username)
        for i, handle in enumerate(args.handles):
            handle = handle.lstrip("@")
            if i > 0:
                human_pause("glance")  # pause between successive lookups
            try:
                uid = cl.user_id_from_username(handle)
                print(f"@{handle} → {uid}")
            except Exception as e:
                print(f"@{handle} → ERROR: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_get_profile(args):
    """Fetch a public Instagram profile: bio + last N post captions."""
    handle = args.handle.lstrip("@")
    max_posts = min(args.posts, 20)  # hard cap at 20

    try:
        cl = get_client(args.username)

        # Call 1: resolve user ID
        try:
            user_id = cl.user_id_from_username(handle)
        except Exception as e:
            print(json.dumps({"error": f"User not found: {handle} — {e}"}))
            sys.exit(1)

        # Call 2: get profile info
        human_pause("think")  # landing on a profile page and reading it
        user = cl.user_info(user_id)

        # Call 3: get recent posts captions
        human_pause("scroll")  # scrolling down through the grid
        try:
            medias = cl.user_medias(user_id, amount=max_posts)
        except Exception:
            medias = []

        posts = []
        for m in medias:
            posts.append({
                "caption": m.caption_text or "",
                "taken_at": m.taken_at.isoformat() if m.taken_at else "",
                "media_type": str(m.media_type),
                "like_count": m.like_count or 0,
            })

        result = {
            "handle": handle,
            "full_name": user.full_name or "",
            "bio": user.biography or "",
            "followers": user.follower_count or 0,
            "following": user.following_count or 0,
            "post_count": user.media_count or 0,
            "is_verified": user.is_verified or False,
            "external_url": str(user.external_url) if user.external_url else "",
            "profile_pic_url": (
                str(getattr(user, "hd_profile_pic_url_info", None).url)
                if getattr(getattr(user, "hd_profile_pic_url_info", None), "url", None)
                else str(getattr(user, "profile_pic_url", "")) if getattr(user, "profile_pic_url", None) else ""
            ),
            "profile_url": f"https://instagram.com/{handle}",
            "posts_fetched": len(posts),
            "posts": posts,
        }

        print(json.dumps(result, ensure_ascii=False, indent=2))
        write_status("idle", "success", f"Profile fetched: @{handle}")

    except Exception as e:
        write_status("idle", "error", str(e))
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


def cmd_read_dms(args):
    """Read recent DM threads, optionally filtered to a specific user."""
    try:
        cl = get_client(args.username)

        if args.handle:
            handle = args.handle.lstrip("@")
            human_pause("glance")  # navigating to the search
            user_id = cl.user_id_from_username(handle)
            human_pause("read")  # opening the thread
            thread = cl.direct_thread_by_participants([user_id])
            messages = []
            for m in thread.messages[:args.limit]:
                messages.append({
                    "from_me": str(m.user_id) == str(cl.user_id),
                    "text": m.text or "",
                    "timestamp": m.timestamp.isoformat() if m.timestamp else "",
                    "item_type": m.item_type,
                })
            print(json.dumps({"handle": handle, "messages": messages}, ensure_ascii=False, indent=2))
        else:
            human_pause("scroll")  # opening inbox and scanning it
            threads = cl.direct_threads(amount=args.limit)
            result = []
            for t in threads:
                users = [u.username for u in t.users]
                last = t.messages[0] if t.messages else None
                result.append({
                    "thread_id": str(t.id),
                    "users": users,
                    "last_message": (last.text or f"[{last.item_type}]") if last else "",
                    "last_ts": last.timestamp.isoformat() if last and last.timestamp else "",
                    "unread": t.unread_count or 0,
                })
            print(json.dumps(result, ensure_ascii=False, indent=2))

        write_status("idle", "success", "DMs read")

    except Exception as e:
        write_status("idle", "error", str(e))
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


def cmd_send_dm(args):
    """Send a DM to an Instagram user."""
    handle = args.handle.lstrip("@")
    try:
        cl = get_client(args.username)
        check_daily_cap(args.username, "sent_dms")
        human_pause("glance")  # navigating to DMs
        user_id = cl.user_id_from_username(handle)
        human_pause("compose")  # opening the compose box
        typing_delay(args.text)  # typing the message at human speed
        thread = cl.direct_send(args.text, user_ids=[user_id])
        record_action(args.username, "sent_dms")
        write_status("idle", "success", f"DM sent to @{handle}")
        print(json.dumps({
            "sent": True,
            "handle": handle,
            "thread_id": str(thread.id),
            "text": args.text,
        }, ensure_ascii=False, indent=2))
    except Exception as e:
        write_status("idle", "error", str(e))
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


def cmd_read_comments(args):
    """Read recent comments on an Instagram media item."""
    try:
        cl = get_client(args.username)
        media_id = resolve_media_id(cl, args.media)
        human_pause("read")  # opening the post and scrolling to comments
        comments = cl.media_comments(media_id, amount=args.limit)
        result = []
        for comment in comments:
            user = getattr(comment, "user", None)
            result.append({
                "comment_id": str(getattr(comment, "pk", "")),
                "text": getattr(comment, "text", "") or "",
                "created_at": (
                    comment.created_at_utc.isoformat()
                    if getattr(comment, "created_at_utc", None)
                    else ""
                ),
                "like_count": getattr(comment, "like_count", 0) or 0,
                "reply_count": getattr(comment, "reply_count", 0) or 0,
                "username": getattr(user, "username", "") if user else "",
                "user_id": str(getattr(user, "pk", "")) if user else "",
            })

        print(json.dumps({
            "media": args.media,
            "media_id": media_id,
            "comments_fetched": len(result),
            "comments": result,
        }, ensure_ascii=False, indent=2))
        write_status("idle", "success", f"Comments read: {media_id}")

    except Exception as e:
        write_status("idle", "error", str(e))
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


def cmd_reply_comment(args):
    """Reply to an Instagram comment or add a top-level comment."""
    try:
        cl = get_client(args.username)
        media_id = resolve_media_id(cl, args.media)
        reply_to = int(args.comment_id) if args.comment_id else None
        human_pause("read")  # reading the comment before replying
        typing_delay(args.text)  # typing the reply at human speed
        comment = cl.media_comment(media_id, args.text, replied_to_comment_id=reply_to)
        write_status("idle", "success", f"Comment posted on {media_id}")
        print(json.dumps({
            "sent": True,
            "media": args.media,
            "media_id": media_id,
            "comment_id": str(getattr(comment, "pk", "")),
            "replied_to_comment_id": str(reply_to) if reply_to else "",
            "text": getattr(comment, "text", args.text) or args.text,
        }, ensure_ascii=False, indent=2))
    except Exception as e:
        write_status("idle", "error", str(e))
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


def cmd_status(args):
    """Show current agent status."""
    try:
        data = json.loads(STATUS_PATH.read_text())
        print(json.dumps(data, indent=2))
    except FileNotFoundError:
        print("No status file found.")


# ── Main ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Instagram Agent — post, story, reel, tag")
    parser.add_argument("--username", default=IG_USERNAME, help="Instagram account (overrides IG_USERNAME env var)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # login
    p_login = subparsers.add_parser("login", help="Authenticate and save session")
    p_login.add_argument("--code", default=None, help="2FA verification code (if required)")
    p_login.set_defaults(func=cmd_login)

    # post-story
    p_story = subparsers.add_parser("post-story", help="Post a video/image to Stories")
    p_story.add_argument("--path", required=True, help="Path to video or image file")
    p_story.add_argument("--tag", nargs="*", default=[], metavar="@HANDLE",
                         help="Instagram handles to tag")
    p_story.set_defaults(func=cmd_post_story)

    # post-feed
    p_feed = subparsers.add_parser("post-feed", help="Post a video/image to feed")
    p_feed.add_argument("--path", required=True, help="Path to video or image file")
    p_feed.add_argument("--caption", default="", help="Post caption")
    p_feed.set_defaults(func=cmd_post_feed)

    # post-reel
    p_reel = subparsers.add_parser("post-reel", help="Post a video as a Reel")
    p_reel.add_argument("--path", required=True, help="Path to video file")
    p_reel.add_argument("--caption", default="", help="Reel caption")
    p_reel.set_defaults(func=cmd_post_reel)

    # post-carousel
    p_carousel = subparsers.add_parser("post-carousel", help="Post multiple images as a carousel (album)")
    p_carousel.add_argument("--paths", nargs="+", required=True, help="Image file paths (2-10)")
    p_carousel.add_argument("--caption", default="", help="Post caption")
    p_carousel.set_defaults(func=cmd_post_carousel)

    # resolve-user
    p_resolve = subparsers.add_parser("resolve-user", help="Resolve username(s) to user IDs")
    p_resolve.add_argument("handles", nargs="+", metavar="@HANDLE")
    p_resolve.set_defaults(func=cmd_resolve_user)

    # get-profile
    p_profile = subparsers.add_parser("get-profile", help="Fetch public profile bio + recent post captions")
    p_profile.add_argument("handle", metavar="@HANDLE", help="Instagram handle")
    p_profile.add_argument("--posts", type=int, default=12, help="Number of recent posts to fetch (default: 12, max: 20)")
    p_profile.set_defaults(func=cmd_get_profile)

    # read-dms
    p_read_dms = subparsers.add_parser("read-dms", help="Read DM threads")
    p_read_dms.add_argument("--handle", default=None, metavar="@HANDLE",
                            help="Read thread with a specific user (omit for all threads)")
    p_read_dms.add_argument("--limit", type=int, default=20, help="Number of messages/threads (default: 20)")
    p_read_dms.set_defaults(func=cmd_read_dms)

    # send-dm
    p_send_dm = subparsers.add_parser("send-dm", help="Send a DM to a user")
    p_send_dm.add_argument("handle", metavar="@HANDLE", help="Recipient Instagram handle")
    p_send_dm.add_argument("--text", required=True, help="Message text to send")
    p_send_dm.set_defaults(func=cmd_send_dm)

    # read-comments
    p_read_comments = subparsers.add_parser("read-comments", help="Read comments on a post or reel")
    p_read_comments.add_argument("media", help="Media ID, shortcode, or Instagram post URL")
    p_read_comments.add_argument("--limit", type=int, default=20, help="Number of comments to fetch (default: 20)")
    p_read_comments.set_defaults(func=cmd_read_comments)

    # reply-comment
    p_reply_comment = subparsers.add_parser("reply-comment", help="Reply to a comment on a post or reel")
    p_reply_comment.add_argument("media", help="Media ID, shortcode, or Instagram post URL")
    p_reply_comment.add_argument("--text", required=True, help="Reply text to post")
    p_reply_comment.add_argument("--comment-id", default=None, help="Comment ID to reply to (omit to post a top-level comment)")
    p_reply_comment.set_defaults(func=cmd_reply_comment)

    # status
    p_status = subparsers.add_parser("status", help="Show agent status")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
