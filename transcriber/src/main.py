#!/usr/bin/env python3
"""IGVideoTranscriber -- Download Instagram videos and transcribe with Whisper."""

import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
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
STATUS_PATH = AGENT_DIR / "status.json"

VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)


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


def download_post_video(url: str) -> tuple[Path, dict]:
    """Download a video from an Instagram post URL. Returns (video_path, metadata)."""
    import instaloader

    shortcode = extract_shortcode(url)
    video_dir = VIDEOS_DIR / shortcode
    video_dir.mkdir(parents=True, exist_ok=True)

    loader = instaloader.Instaloader(
        download_videos=True,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        dirname_pattern=str(video_dir),
        filename_pattern="{shortcode}",
    )

    ig_username = os.environ.get("IG_USERNAME")
    ig_password = os.environ.get("IG_PASSWORD")
    if ig_username and ig_password:
        try:
            loader.login(ig_username, ig_password)
        except instaloader.exceptions.ConnectionException as e:
            print(f"Warning: Login failed ({e}), continuing anonymously", file=sys.stderr)

    try:
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
    except instaloader.exceptions.LoginRequiredException:
        raise RuntimeError("Profile is private. Login required.")
    except Exception as e:
        raise RuntimeError(f"Could not load post: {e}")

    if not post.is_video:
        raise RuntimeError("Post does not contain a video.")

    loader.download_post(post, target=shortcode)

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
    result = subprocess.run(
        [
            "whisper",
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
    import instaloader

    write_status("working", None, f"Sweeping profile @{args.username}")
    try:
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
        if ig_username and ig_password:
            try:
                loader.login(ig_username, ig_password)
            except Exception as e:
                print(f"Warning: Login failed ({e}), continuing anonymously", file=sys.stderr)

        try:
            profile = instaloader.Profile.from_username(loader.context, args.username)
        except instaloader.exceptions.LoginRequiredException:
            raise RuntimeError("Profile is private. Login required.")

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
