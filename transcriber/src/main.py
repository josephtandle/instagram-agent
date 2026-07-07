#!/usr/bin/env python3
from __future__ import annotations
"""IGVideoTranscriber -- Download Instagram videos and transcribe with Whisper."""

import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path.home() / ".myos" / "workspace" / ".env"
if not ENV_PATH.exists():
    ENV_PATH = Path.home() / ".myos" / "workspace" / ".env"
load_dotenv(ENV_PATH)

AGENT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = AGENT_DIR / "data"
VIDEOS_DIR = DATA_DIR / "videos"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
SESSIONS_DIR = DATA_DIR / "sessions"
HEALTH_PATH = DATA_DIR / "health.json"
STATUS_PATH = AGENT_DIR / "status.json"

VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def write_status(status: str, result=None, message=None):
    """Update status.json with current state."""
    data = {
        "agentId": "ig-video-transcriber",
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


def extract_shortcode(url: str) -> str:
    """Extract shortcode from an Instagram URL."""
    url = url.rstrip("/")
    parts = url.split("/")
    for i, part in enumerate(parts):
        if part in ("p", "reel", "reels", "tv") and i + 1 < len(parts):
            return parts[i + 1]
    raise ValueError(f"Could not extract shortcode from URL: {url}")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _load_health() -> dict:
    try:
        return json.loads(HEALTH_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_health(health: dict) -> None:
    HEALTH_PATH.write_text(json.dumps(health, indent=2) + "\n")


def set_download_cooldown(seconds: int, reason: str, trigger: str) -> dict:
    health = _load_health()
    health.update({
        "blocked_until": (_utc_now() + timedelta(seconds=max(60, seconds))).isoformat(),
        "reason": reason,
        "trigger": trigger,
        "updated_at": _utc_now().isoformat(),
    })
    _save_health(health)
    return health


def clear_download_cooldown() -> None:
    health = _load_health()
    for key in ("blocked_until", "reason", "trigger"):
        health.pop(key, None)
    health["updated_at"] = _utc_now().isoformat()
    _save_health(health)


def mark_download_success(source: str) -> None:
    health = _load_health()
    health["last_success_at"] = _utc_now().isoformat()
    health["last_success_source"] = source
    for key in ("blocked_until", "reason", "trigger"):
        health.pop(key, None)
    health["updated_at"] = _utc_now().isoformat()
    _save_health(health)


def get_active_download_cooldown() -> dict | None:
    health = _load_health()
    blocked_until = _parse_iso_datetime(health.get("blocked_until"))
    if not blocked_until:
        return None
    now = _utc_now()
    if blocked_until <= now:
        clear_download_cooldown()
        return None
    return {
        "blocked_until": blocked_until.isoformat(),
        "remaining_seconds": int((blocked_until - now).total_seconds()),
        "reason": health.get("reason", "cooldown_active"),
        "trigger": health.get("trigger", ""),
    }


def enforce_download_guard() -> None:
    cooldown = get_active_download_cooldown()
    if cooldown:
        raise RuntimeError(
            f"Instagram transcriber cooling down until {cooldown['blocked_until']} "
            f"({cooldown['reason']}, trigger={cooldown['trigger']})"
        )


def _cooldown_for_error(exc: Exception) -> tuple[int, str, str] | None:
    message = str(exc).lower()
    if "403 forbidden" in message or "forbidden" in message:
        return (12 * 3600, "forbidden_response", "forbidden")
    if "429" in message or "rate limit" in message or "too many requests" in message:
        return (8 * 3600, "rate_limited", "rate_limit")
    if "login required" in message or "checkpoint" in message or "challenge" in message:
        return (24 * 3600, "authentication_challenge", "challenge")
    if "please wait" in message or "try again later" in message:
        return (6 * 3600, "temporary_throttle", "throttle")
    return None


def apply_download_cooldown(exc: Exception) -> dict | None:
    policy = _cooldown_for_error(exc)
    if not policy:
        return None
    seconds, reason, trigger = policy
    return set_download_cooldown(seconds, reason, trigger)


def _session_path(username: str) -> Path:
    return SESSIONS_DIR / f"{username}.session"


def build_loader():
    import instaloader

    loader = instaloader.Instaloader(
        download_videos=True,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
    )

    ig_username = os.environ.get("IG_USERNAME")
    ig_password = os.environ.get("IG_PASSWORD")
    if not ig_username:
        raise RuntimeError(
            "IG_USERNAME is required for transcriber downloads. Anonymous Instagram downloads are disabled to reduce block risk."
        )

    session_path = _session_path(ig_username)
    if session_path.exists():
        try:
            loader.load_session_from_file(ig_username, filename=str(session_path))
            return loader
        except Exception:
            pass

    if not ig_password:
        raise RuntimeError(
            "No reusable transcriber session found and IG_PASSWORD is unavailable. "
            "Refusing anonymous fallback to reduce block risk."
        )

    loader.login(ig_username, ig_password)
    loader.save_session_to_file(filename=str(session_path))
    return loader


def download_post_video(url: str) -> tuple[Path, dict]:
    """Download a video from an Instagram post URL. Returns (video_path, metadata)."""
    enforce_download_guard()
    shortcode = extract_shortcode(url)
    video_dir = VIDEOS_DIR / shortcode
    video_dir.mkdir(parents=True, exist_ok=True)

    loader = build_loader()
    loader.dirname_pattern = str(video_dir)
    loader.filename_pattern = "{shortcode}"

    try:
        import instaloader
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
    except Exception as e:
        apply_download_cooldown(e)
        raise RuntimeError(f"Could not load post: {e}")

    if not post.is_video:
        raise RuntimeError("Post does not contain a video.")

    try:
        loader.download_post(post, target=shortcode)
    except Exception as e:
        apply_download_cooldown(e)
        raise RuntimeError(f"Video download failed: {e}")

    video_files = list(video_dir.glob("*.mp4"))
    if not video_files:
        raise RuntimeError("Video download failed -- no .mp4 file found.")

    metadata = {
        "shortcode": shortcode,
        "url": url,
        "username": post.owner_username,
        "caption": (post.caption or "")[:500],
        "downloadedAt": datetime.now(timezone.utc).isoformat(),
    }

    return video_files[0], metadata


def transcribe_with_local_whisper(video_path: Path, model: str = "base") -> str:
    """Transcribe using local whisper CLI."""
    output_dir = video_path.parent
    whisper_bin = os.environ.get("MYOS_WHISPER_BIN", "whisper")
    result = subprocess.run(
        [
            whisper_bin,
            str(video_path),
            "--model", model,
            "--output_dir", str(output_dir),
            "--output_format", "txt",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Whisper failed: {result.stderr}")

    txt_files = list(output_dir.glob("*.txt"))
    if not txt_files:
        raise RuntimeError("Whisper produced no output file.")

    return txt_files[0].read_text().strip()


def transcribe_with_openai(video_path: Path) -> str:
    """Transcribe using OpenAI Whisper API."""
    from openai import OpenAI

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    client = OpenAI(api_key=api_key)
    with open(video_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
        )
    return response.text.strip()


def transcribe_video(video_path: Path, model: str = "base") -> tuple[str, str]:
    """Transcribe a video file. Returns (transcript, model_used).

    Prefer local whisper for unattended work, then fall back to OpenAI if needed.
    """
    try:
        transcript = transcribe_with_local_whisper(video_path, model)
        return transcript, f"whisper-{model}"
    except Exception as e:
        print(f"Local whisper failed ({e}), checking OpenAI fallback", file=sys.stderr)

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        try:
            transcript = transcribe_with_openai(video_path)
            return transcript, "whisper-1-api"
        except Exception as e:
            raise RuntimeError(f"All transcription methods failed. OpenAI fallback error: {e}")

    raise RuntimeError("All transcription methods failed. OPENAI_API_KEY not set and local whisper was unavailable.")


def save_transcript(metadata: dict, transcript: str, model_used: str) -> Path:
    """Save transcript JSON and return the path."""
    shortcode = metadata["shortcode"]
    data = {
        **metadata,
        "transcribedAt": datetime.now(timezone.utc).isoformat(),
        "transcript": transcript,
        "model": model_used,
    }
    path = TRANSCRIPTS_DIR / f"{shortcode}.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


# ── Commands ──────────────────────────────────────────────────────


def cmd_transcribe(args):
    """Download and transcribe a single Instagram post."""
    write_status("working", None, f"Transcribing {args.url}")
    try:
        video_path, metadata = download_post_video(args.url)
        transcript, model_used = transcribe_video(video_path, args.model)
        path = save_transcript(metadata, transcript, model_used)
        mark_download_success("transcribe")
        write_status("idle", "success", f"Transcribed {metadata['shortcode']}")
        print(f"\n--- Transcript ({metadata['shortcode']}) ---")
        print(transcript)
        print(f"\nSaved to: {path}")
    except Exception as e:
        write_status("idle", "error", str(e))
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_transcribe_profile(args):
    """Download and transcribe recent videos from a profile."""
    write_status("working", None, f"Sweeping profile @{args.username}")
    try:
        enforce_download_guard()
        import instaloader
        loader = build_loader()

        try:
            profile = instaloader.Profile.from_username(loader.context, args.username)
        except Exception as e:
            apply_download_cooldown(e)
            raise RuntimeError(f"Could not load profile: {e}")

        count = 0
        for post in profile.get_posts():
            if count >= args.limit:
                break
            if not post.is_video:
                continue
            if args.reels_only and post.typename != "GraphVideo":
                continue

            shortcode = post.shortcode
            existing = TRANSCRIPTS_DIR / f"{shortcode}.json"
            if existing.exists():
                print(f"Skipping {shortcode} (already transcribed)")
                count += 1
                continue

            video_dir = VIDEOS_DIR / shortcode
            video_dir.mkdir(parents=True, exist_ok=True)
            loader.dirname_pattern = str(video_dir)
            loader.filename_pattern = "{shortcode}"

            try:
                loader.download_post(post, target=shortcode)
            except Exception as e:
                apply_download_cooldown(e)
                print(f"Failed to download {shortcode}: {e}", file=sys.stderr)
                continue

            video_files = list(video_dir.glob("*.mp4"))
            if not video_files:
                print(f"No video file for {shortcode}", file=sys.stderr)
                continue

            metadata = {
                "shortcode": shortcode,
                "url": f"https://www.instagram.com/p/{shortcode}/",
                "username": args.username,
                "caption": (post.caption or "")[:500],
                "downloadedAt": datetime.now(timezone.utc).isoformat(),
            }

            try:
                transcript, model_used = transcribe_video(video_files[0], args.model)
                save_transcript(metadata, transcript, model_used)
                mark_download_success("transcribe_profile")
                print(f"Transcribed: {shortcode}")
            except Exception as e:
                print(f"Failed to transcribe {shortcode}: {e}", file=sys.stderr)

            count += 1

        write_status("idle", "success", f"Swept @{args.username}: {count} videos")
        print(f"\nDone. Processed {count} videos from @{args.username}")
    except Exception as e:
        write_status("idle", "error", str(e))
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_transcribe_file(args):
    """Transcribe a local video file."""
    video_path = Path(args.path).resolve()
    if not video_path.exists():
        print(f"Error: File not found: {video_path}", file=sys.stderr)
        sys.exit(1)

    write_status("working", None, f"Transcribing local file {video_path.name}")
    try:
        transcript, model_used = transcribe_video(video_path, args.model)
        shortcode = video_path.stem
        metadata = {
            "shortcode": shortcode,
            "url": f"file://{video_path}",
            "username": "local",
            "caption": "",
            "downloadedAt": datetime.now(timezone.utc).isoformat(),
        }
        path = save_transcript(metadata, transcript, model_used)
        mark_download_success("transcribe_file")
        write_status("idle", "success", f"Transcribed local file {video_path.name}")
        print(f"\n--- Transcript ({shortcode}) ---")
        print(transcript)
        print(f"\nSaved to: {path}")
    except Exception as e:
        write_status("idle", "error", str(e))
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_list(args):
    """List all previously transcribed videos."""
    files = sorted(TRANSCRIPTS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        print("No transcripts found.")
        return

    for f in files:
        try:
            data = json.loads(f.read_text())
            preview = (data.get("transcript", "")[:80] + "...") if len(data.get("transcript", "")) > 80 else data.get("transcript", "")
            print(f"  {data.get('shortcode', '?'):20s}  @{data.get('username', '?'):20s}  {data.get('transcribedAt', '?')[:10]}  {preview}")
        except (json.JSONDecodeError, KeyError):
            print(f"  {f.stem:20s}  (invalid JSON)")


def cmd_get(args):
    """Retrieve a previously saved transcript."""
    path = TRANSCRIPTS_DIR / f"{args.id}.json"
    if not path.exists():
        print(f"Error: No transcript found for shortcode '{args.id}'", file=sys.stderr)
        sys.exit(1)

    data = json.loads(path.read_text())
    print(json.dumps(data, indent=2))


# ── Main ──────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="IGVideoTranscriber -- Instagram video transcription")
    parser.add_argument("--model", default="base", choices=["base", "small", "medium", "large"],
                        help="Whisper model size (default: base)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_transcribe = subparsers.add_parser("transcribe", help="Transcribe a single Instagram post")
    p_transcribe.add_argument("--url", required=True, help="Instagram post URL")
    p_transcribe.set_defaults(func=cmd_transcribe)

    p_profile = subparsers.add_parser("transcribe-profile", help="Transcribe videos from a profile")
    p_profile.add_argument("--username", required=True, help="Instagram username")
    p_profile.add_argument("--limit", type=int, default=5, help="Max videos to process (default: 5)")
    p_profile.add_argument("--reels-only", action="store_true", help="Only process reels")
    p_profile.set_defaults(func=cmd_transcribe_profile)

    p_file = subparsers.add_parser("transcribe-file", help="Transcribe a local video file")
    p_file.add_argument("--path", required=True, help="Path to video file")
    p_file.set_defaults(func=cmd_transcribe_file)

    p_list = subparsers.add_parser("list", help="List all transcripts")
    p_list.set_defaults(func=cmd_list)

    p_get = subparsers.add_parser("get", help="Get a specific transcript")
    p_get.add_argument("--id", required=True, help="Post shortcode")
    p_get.set_defaults(func=cmd_get)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
