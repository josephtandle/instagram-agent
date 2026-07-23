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
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

AGENT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = AGENT_DIR / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
STATUS_PATH = AGENT_DIR / "status.json"
HEALTH_PATH = DATA_DIR / "health.json"
LATEST_OFFICIAL_STATS_PATH = DATA_DIR / "latest_official_stats.json"
CONFIG_DIR = Path.home() / ".instagram-agent"
DEVICE_PATH = CONFIG_DIR / "device.json"
USAGE_PATH = DATA_DIR / "usage.json"

DAILY_CAPS = {
    "sent_dms": 20,
    "profile_reads": 150,
    "dm_thread_reads": 80,
    "comment_reads": 120,
    "user_resolves": 120,
    "comment_writes": 25,
    "content_posts": 12,
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
        # Workspace .env holds META_IG_ACCESS_TOKEN / META_IG_ACCOUNT_ID, which the
        # agent-local files do not. Load it too rather than duplicating secrets.
        Path.home() / ".myos" / "workspace" / ".env",
    ])

    # Load every candidate that exists, in order. load_dotenv does not override
    # already-set vars, so earlier files keep precedence and later ones only fill gaps.
    first_loaded = None
    for env_path in candidates:
        if env_path.exists():
            load_dotenv(env_path)
            if first_loaded is None:
                first_loaded = env_path

    return first_loaded


ENV_PATH = load_environment()

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

IG_USERNAME = os.environ.get("IG_USERNAME", "")
META_IG_ACCESS_TOKEN = os.environ.get("META_IG_ACCESS_TOKEN", "")
META_IG_ACCOUNT_ID = os.environ.get("META_IG_ACCOUNT_ID", "")
BALI_TZ = ZoneInfo("Asia/Makassar")
GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


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


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_health() -> dict:
    try:
        return json.loads(HEALTH_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"accounts": {}}


def _save_health(health: dict) -> None:
    HEALTH_PATH.write_text(json.dumps(health, indent=2) + "\n")


def _account_health(username: str) -> dict:
    health = _load_health()
    return health.setdefault("accounts", {}).setdefault(username, {})


def _write_account_health(username: str, payload: dict) -> None:
    health = _load_health()
    health.setdefault("accounts", {})[username] = payload
    _save_health(health)


def _summarize_official_stats(result: dict) -> dict:
    windows = result.get("windows", {})
    yesterday = windows.get("yesterday", {})
    trailing_7d = windows.get("trailing_7d", {})
    trailing_30d = windows.get("trailing_30d", {})
    account = result.get("account", {})
    return {
        "followers": int(account.get("followers") or 0),
        "media": int(account.get("media") or 0),
        "yesterday": {
            "views": int(yesterday.get("views") or 0),
            "comments": int(yesterday.get("comments") or 0),
            "likes": int(yesterday.get("likes") or 0),
            "posts": int(yesterday.get("posts") or 0),
        },
        "trailing_7d": {
            "views": int(trailing_7d.get("views") or 0),
            "comments": int(trailing_7d.get("comments") or 0),
            "likes": int(trailing_7d.get("likes") or 0),
            "posts": int(trailing_7d.get("posts") or 0),
        },
        "trailing_30d": {
            "views": int(trailing_30d.get("views") or 0),
            "comments": int(trailing_30d.get("comments") or 0),
            "likes": int(trailing_30d.get("likes") or 0),
            "posts": int(trailing_30d.get("posts") or 0),
        },
    }


def _persist_official_stats(username: str, result: dict) -> None:
    payload = _account_health(username)
    payload["last_official_stats_at"] = _utc_now().isoformat()
    payload["last_official_stats_source"] = result.get("source", "meta_graph_api")
    payload["official_stats_status"] = result.get("status", "unknown")
    payload["official_stats_account"] = result.get("account", {})
    payload["official_stats_windows"] = result.get("windows", {})
    payload["official_stats_media_fetched"] = int(result.get("mediaFetched") or 0)
    payload["official_stats_summary"] = _summarize_official_stats(result)
    if result.get("error"):
        payload["official_stats_error"] = result.get("error")
    else:
        payload.pop("official_stats_error", None)
    payload["updated_at"] = _utc_now().isoformat()
    _write_account_health(username, payload)
    LATEST_OFFICIAL_STATS_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


def set_account_cooldown(username: str, seconds: int, reason: str, source: str, trigger: str) -> dict:
    until = _utc_now() + timedelta(seconds=max(60, seconds))
    payload = _account_health(username)
    payload.update({
        "blocked_until": until.isoformat(),
        "reason": reason,
        "source": source,
        "trigger": trigger,
        "updated_at": _utc_now().isoformat(),
    })
    _write_account_health(username, payload)
    return payload


def clear_account_cooldown(username: str) -> None:
    payload = _account_health(username)
    payload.pop("blocked_until", None)
    payload.pop("reason", None)
    payload.pop("source", None)
    payload.pop("trigger", None)
    payload["updated_at"] = _utc_now().isoformat()
    _write_account_health(username, payload)


def get_active_cooldown(username: str) -> dict | None:
    payload = _account_health(username)
    blocked_until = _parse_iso_datetime(payload.get("blocked_until"))
    if not blocked_until:
        return None
    now = _utc_now()
    if blocked_until <= now:
        clear_account_cooldown(username)
        return None
    remaining = int((blocked_until - now).total_seconds())
    return {
        "blocked_until": blocked_until.isoformat(),
        "remaining_seconds": remaining,
        "reason": payload.get("reason", "cooldown_active"),
        "source": payload.get("source", "instagram"),
        "trigger": payload.get("trigger", ""),
    }


def check_daily_cap(username: str, action: str, amount: int = 1) -> None:
    cap = DAILY_CAPS.get(action)
    if cap is None:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage = _load_usage()
    count = usage.get(today, {}).get(username, {}).get(action, 0)
    if count + amount > cap:
        print(
            f"Error: Daily cap reached for {action} ({count}/{cap} used for @{username}). "
            "Resets after midnight UTC.",
            file=sys.stderr,
        )
        sys.exit(1)


def record_action(username: str, action: str, amount: int = 1) -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage = _load_usage()
    usage.setdefault(today, {}).setdefault(username, {})[action] = (
        usage.get(today, {}).get(username, {}).get(action, 0) + amount
    )
    _save_usage(usage)


def enforce_action_guard(username: str, action: str) -> None:
    cooldown = get_active_cooldown(username)
    if cooldown:
        print(
            json.dumps({
                "error": "instagram_account_cooling_down",
                "username": username,
                **cooldown,
            }, ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        sys.exit(1)
    check_daily_cap(username, action)


def _protective_cooldown_for_error(exc: Exception) -> tuple[int, str, str] | None:
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    if "challenge" in name or "challenge" in message:
        return (24 * 3600, "challenge_required", "challenge")
    if "feedbackrequired" in name or "feedback required" in message or "feedback_required" in message:
        return (12 * 3600, "feedback_blocked", "feedback")
    if "ratelimit" in name or "rate limit" in message or "429" in message:
        return (6 * 3600, "rate_limited", "rate_limit")
    if "403 forbidden" in message or "forbidden" in message:
        return (6 * 3600, "forbidden_response", "forbidden")
    if "please wait" in message or "try again later" in message:
        return (4 * 3600, "temporary_throttle", "throttle")
    return None


def apply_protective_cooldown(username: str, source: str, exc: Exception) -> dict | None:
    policy = _protective_cooldown_for_error(exc)
    if not policy:
        return None
    seconds, reason, trigger = policy
    return set_account_cooldown(username, seconds, reason, source, trigger)


def mark_account_success(username: str, source: str) -> None:
    payload = _account_health(username)
    payload["last_success_at"] = _utc_now().isoformat()
    payload["last_success_source"] = source
    payload["updated_at"] = _utc_now().isoformat()
    _write_account_health(username, payload)


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
            mark_account_success(username, "session_reuse")
            return cl
        except ChallengeRequired:
            apply_protective_cooldown(username, "session_reuse", ChallengeRequired("challenge required"))
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
        apply_protective_cooldown(username, "login", ChallengeRequired("challenge required"))
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
        apply_protective_cooldown(username, "login", e)
        print(f"Error: Instagram blocked this action. Try again later. Detail: {e}", file=sys.stderr)
        sys.exit(1)
    except RateLimitError:
        apply_protective_cooldown(username, "login", RateLimitError("rate limit"))
        print("Error: Instagram rate limit hit. Wait a few minutes before trying again.", file=sys.stderr)
        sys.exit(1)

    cl.dump_settings(session_path)
    mark_account_success(username, "login")
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


def _window_payload(start: datetime, end: datetime, label: str) -> dict:
    return {
        "label": label,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "posts": 0,
        "views": 0,
        "comments": 0,
        "likes": 0,
        "hasViewData": False,
    }


def _get_official_windows(now: datetime | None = None) -> dict:
    now_bali = (now or datetime.now(BALI_TZ)).astimezone(BALI_TZ)
    today_start = now_bali.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    days_since_monday = today_start.weekday()
    this_monday = today_start - timedelta(days=days_since_monday)
    last_monday = this_monday - timedelta(days=7)
    prior_monday = this_monday - timedelta(days=14)
    month_start = today_start.replace(day=1)

    return {
        "yesterday": _window_payload(yesterday_start, today_start, "yesterday"),
        "completedWeek": _window_payload(last_monday, this_monday, "last completed Monday-to-Monday week"),
        "previousWeek": _window_payload(prior_monday, last_monday, "previous Monday-to-Monday week"),
        "monthToDate": _window_payload(month_start, now_bali, "month to date"),
    }


def _parse_meta_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(BALI_TZ)
    except ValueError:
        return None


def _graph_get(path_or_url: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    params["access_token"] = META_IG_ACCESS_TOKEN
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        url = path_or_url
    else:
        url = f"{GRAPH_API_BASE}/{path_or_url.lstrip('/')}"
        url = f"{url}?{urlencode(params)}"

    req = Request(url, headers={"User-Agent": "myos-instagram-agent/1.0"})
    try:
        with urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body).get("error", {}).get("message", body)
        except json.JSONDecodeError:
            detail = body
        raise RuntimeError(f"Meta Graph API error: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Meta Graph API request failed: {exc.reason}") from exc


def _fetch_media_view_count(media_id: str) -> int | None:
    for metric in ("views", "plays"):
        try:
            data = _graph_get(f"{media_id}/insights", {"metric": metric})
        except Exception:
            continue
        entry = next((item for item in data.get("data", []) if item.get("name") == metric), None)
        value = (entry.get("values") or [{}])[0].get("value") if entry else None
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _add_media_to_window(window: dict, item: dict) -> None:
    window["posts"] += 1
    window["comments"] += int(item.get("comments") or 0)
    window["likes"] += int(item.get("likes") or 0)
    if isinstance(item.get("views"), int):
        window["views"] += item["views"]
        window["hasViewData"] = True


def _summarize_media(media: list[dict], windows: dict) -> dict:
    for item in media:
        published_at = item.get("publishedAt")
        if not published_at:
            continue
        for window in windows.values():
            start = datetime.fromisoformat(window["start"])
            end = datetime.fromisoformat(window["end"])
            if start <= published_at < end:
                _add_media_to_window(window, item)
    return windows


def cmd_official_stats(args):
    """Read Instagram account stats via the official Meta Graph API only."""
    if not META_IG_ACCESS_TOKEN or not META_IG_ACCOUNT_ID:
        result = {
            "status": "unavailable",
            "source": "meta_graph_api",
            "readOnly": True,
            "configured": {
                "META_IG_ACCESS_TOKEN": bool(META_IG_ACCESS_TOKEN),
                "META_IG_ACCOUNT_ID": bool(META_IG_ACCOUNT_ID),
            },
            "error": "META_IG_ACCESS_TOKEN and META_IG_ACCOUNT_ID are required for official read-only Instagram stats.",
        }
        _persist_official_stats(args.username, result)
        write_status("idle", "partial", "Official Instagram stats unavailable")
        print(json.dumps(result, indent=2))
        return

    try:
        account = _graph_get(
            META_IG_ACCOUNT_ID,
            {"fields": "id,username,name,followers_count,media_count"},
        )
        windows = _get_official_windows()
        oldest_needed = min(datetime.fromisoformat(w["start"]) for w in windows.values())
        media = []
        next_url = None
        fields = "id,timestamp,media_type,like_count,comments_count,permalink"

        for page in range(4):
            if next_url:
                data = _graph_get(next_url)
            else:
                data = _graph_get(
                    f"{META_IG_ACCOUNT_ID}/media",
                    {"fields": fields, "limit": "100"},
                )

            for raw in data.get("data", []):
                published_at = _parse_meta_datetime(raw.get("timestamp"))
                if not published_at:
                    continue
                if published_at < oldest_needed:
                    continue
                media.append({
                    "id": raw.get("id", ""),
                    "publishedAt": published_at,
                    "mediaType": raw.get("media_type", ""),
                    "comments": int(raw.get("comments_count") or 0),
                    "likes": int(raw.get("like_count") or 0),
                    "permalink": raw.get("permalink", ""),
                    "views": _fetch_media_view_count(raw.get("id", "")),
                })

            oldest_on_page = media[-1]["publishedAt"] if media else None
            if oldest_on_page and oldest_on_page < oldest_needed:
                break
            next_url = data.get("paging", {}).get("next")
            if not next_url:
                break

        summarized = _summarize_media(media, windows)
        result = {
            "status": "ok",
            "source": "meta_graph_api",
            "readOnly": True,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "account": {
                "id": account.get("id", META_IG_ACCOUNT_ID),
                "username": account.get("username") or account.get("name") or "",
                "followers": int(account.get("followers_count") or 0),
                "media": int(account.get("media_count") or 0),
            },
            "windows": summarized,
            "mediaFetched": len(media),
            "media": [
                {
                    **item,
                    "publishedAt": item["publishedAt"].isoformat(),
                }
                for item in media[:25]
            ] if args.include_media else [],
        }
        _persist_official_stats(args.username, result)
        mark_account_success(args.username, "official_stats")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        write_status("idle", "success", "Official Instagram stats fetched")
    except Exception as e:
        result = {
            "status": "error",
            "source": "meta_graph_api",
            "readOnly": True,
            "error": str(e),
        }
        _persist_official_stats(args.username, result)
        write_status("idle", "error", str(e))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)


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
    except Exception as e:
        apply_protective_cooldown(args.username, "login", e)
        write_status("idle", "error", str(e))
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    cl.dump_settings(session_path)
    mark_account_success(args.username, "login")
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
        enforce_action_guard(args.username, "content_posts")
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
        record_action(args.username, "content_posts")
        mark_account_success(args.username, "post_story")
        print(f"\nStory posted successfully!")
        print(f"Media ID: {media.pk}")
        if mentions:
            print(f"Tagged: {', '.join('@' + m['username'] for m in mentions)}")

    except Exception as e:
        apply_protective_cooldown(args.username, "post_story", e)
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
        enforce_action_guard(args.username, "content_posts")
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
        record_action(args.username, "content_posts")
        mark_account_success(args.username, "post_feed")
        print(f"\nFeed post published!")
        print(f"Media ID: {media.pk}")

    except Exception as e:
        apply_protective_cooldown(args.username, "post_feed", e)
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
        enforce_action_guard(args.username, "content_posts")
        cl = get_client(args.username)
        caption = args.caption or ""
        human_pause("post")  # reviewing reel and caption before posting
        print("Uploading reel...")
        media = cl.clip_upload(path, caption=caption)
        write_status("idle", "success", f"Reel posted: {media.pk}")
        record_action(args.username, "content_posts")
        mark_account_success(args.username, "post_reel")
        print(f"\nReel posted!")
        print(f"Media ID: {media.pk}")

    except Exception as e:
        apply_protective_cooldown(args.username, "post_reel", e)
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
        enforce_action_guard(args.username, "content_posts")
        cl = get_client(args.username)
        caption = args.caption or ""
        human_pause("post")  # reviewing carousel images and caption before posting
        print(f"Uploading carousel ({len(paths)} images)...")
        media = cl.album_upload(paths, caption=caption)
        write_status("idle", "success", f"Carousel posted: {media.pk}")
        record_action(args.username, "content_posts")
        mark_account_success(args.username, "post_carousel")
        print(f"\nCarousel posted!")
        print(f"Media ID: {media.pk}")
    except Exception as e:
        apply_protective_cooldown(args.username, "post_carousel", e)
        write_status("idle", "error", str(e))
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_resolve_user(args):
    """Resolve an Instagram username to a user ID."""
    try:
        enforce_action_guard(args.username, "user_resolves")
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
        record_action(args.username, "user_resolves", amount=max(1, len(args.handles)))
        mark_account_success(args.username, "resolve_user")
    except Exception as e:
        apply_protective_cooldown(args.username, "resolve_user", e)
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_get_profile(args):
    """Fetch a public Instagram profile: bio + last N post captions."""
    handle = args.handle.lstrip("@")
    max_posts = min(args.posts, 20)  # hard cap at 20

    try:
        enforce_action_guard(args.username, "profile_reads")
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
        record_action(args.username, "profile_reads")
        mark_account_success(args.username, "get_profile")

    except Exception as e:
        apply_protective_cooldown(args.username, "get_profile", e)
        write_status("idle", "error", str(e))
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


def cmd_read_dms(args):
    """Read recent DM threads, optionally filtered to a specific user."""
    try:
        enforce_action_guard(args.username, "dm_thread_reads")
        cl = get_client(args.username)

        if args.handle:
            handle = args.handle.lstrip("@")
            human_pause("glance")  # navigating to the search
            user_id = cl.user_id_from_username(handle)
            human_pause("read")  # opening the thread
            # direct_thread_by_participants returns a raw dict (thread_id lookup only);
            # direct_thread() returns the typed DirectThread object with parsed .messages.
            thread_lookup = cl.direct_thread_by_participants([user_id])
            thread_id = thread_lookup.get("thread_id") or thread_lookup.get("thread", {}).get("thread_id")
            thread = cl.direct_thread(thread_id, amount=args.limit)
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
                    "unread": getattr(t, "unread_count", None) or 0,
                })
            print(json.dumps(result, ensure_ascii=False, indent=2))

        write_status("idle", "success", "DMs read")
        record_action(args.username, "dm_thread_reads")
        mark_account_success(args.username, "read_dms")

    except Exception as e:
        apply_protective_cooldown(args.username, "read_dms", e)
        write_status("idle", "error", str(e))
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


def cmd_send_dm(args):
    """Send a DM to an Instagram user."""
    handle = args.handle.lstrip("@")
    try:
        cl = get_client(args.username)
        enforce_action_guard(args.username, "sent_dms")
        human_pause("glance")  # navigating to DMs
        user_id = cl.user_id_from_username(handle)
        human_pause("compose")  # opening the compose box
        typing_delay(args.text)  # typing the message at human speed
        thread = cl.direct_send(args.text, user_ids=[user_id])
        record_action(args.username, "sent_dms")
        mark_account_success(args.username, "send_dm")
        write_status("idle", "success", f"DM sent to @{handle}")
        print(json.dumps({
            "sent": True,
            "handle": handle,
            "thread_id": str(thread.id),
            "text": args.text,
        }, ensure_ascii=False, indent=2))
    except Exception as e:
        apply_protective_cooldown(args.username, "send_dm", e)
        write_status("idle", "error", str(e))
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


def cmd_read_comments(args):
    """Read recent comments on an Instagram media item."""
    try:
        enforce_action_guard(args.username, "comment_reads")
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
        record_action(args.username, "comment_reads")
        mark_account_success(args.username, "read_comments")

    except Exception as e:
        apply_protective_cooldown(args.username, "read_comments", e)
        write_status("idle", "error", str(e))
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


def cmd_reply_comment(args):
    """Reply to an Instagram comment or add a top-level comment."""
    try:
        enforce_action_guard(args.username, "comment_writes")
        cl = get_client(args.username)
        media_id = resolve_media_id(cl, args.media)
        reply_to = int(args.comment_id) if args.comment_id else None
        human_pause("read")  # reading the comment before replying
        typing_delay(args.text)  # typing the reply at human speed
        comment = cl.media_comment(media_id, args.text, replied_to_comment_id=reply_to)
        write_status("idle", "success", f"Comment posted on {media_id}")
        record_action(args.username, "comment_writes")
        mark_account_success(args.username, "reply_comment")
        print(json.dumps({
            "sent": True,
            "media": args.media,
            "media_id": media_id,
            "comment_id": str(getattr(comment, "pk", "")),
            "replied_to_comment_id": str(reply_to) if reply_to else "",
            "text": getattr(comment, "text", args.text) or args.text,
        }, ensure_ascii=False, indent=2))
    except Exception as e:
        apply_protective_cooldown(args.username, "reply_comment", e)
        write_status("idle", "error", str(e))
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


def cmd_status(args):
    """Show current agent status."""
    try:
        data = json.loads(STATUS_PATH.read_text())
        today = _utc_now().strftime("%Y-%m-%d")
        usage = _load_usage().get(today, {}).get(args.username or IG_USERNAME, {})
        health = _account_health(args.username or IG_USERNAME)
        active_cooldown = get_active_cooldown(args.username or IG_USERNAME)
        print(json.dumps({
            **data,
            "account": args.username or IG_USERNAME,
            "todayUsage": usage,
            "health": health,
            "activeCooldown": active_cooldown,
        }, indent=2))
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

    # official-stats
    p_official_stats = subparsers.add_parser(
        "official-stats",
        help="Read account, yesterday, weekly, and month-to-date stats via Meta Graph API",
    )
    p_official_stats.add_argument(
        "--include-media",
        action="store_true",
        help="Include up to 25 fetched media rows in the JSON output",
    )
    p_official_stats.set_defaults(func=cmd_official_stats)

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
