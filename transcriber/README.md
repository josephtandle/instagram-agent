# IGVideoTranscriber

Downloads Instagram videos using InstaLoader and transcribes them with Whisper.

## Requirements

- Python 3.10+
- `instaloader` (pip) -- downloads public Instagram videos anonymously
- `whisper` CLI (`MYOS_WHISPER_BIN` can override the detected binary path) -- preferred local transcription
- `OPENAI_API_KEY` (optional) -- fallback if local whisper is unavailable

## Installation

```bash
cd ~/.myos/workspace/agents/ig-video-transcriber
pip3 install -r requirements.txt
```

## Usage

### Transcribe a single post

```bash
python3 src/main.py transcribe --url https://www.instagram.com/p/ABC123/
```

### Transcribe recent videos from a profile

```bash
python3 src/main.py transcribe-profile --username hubermanlab --limit 10 --reels-only
```

### Transcribe a local video file

```bash
python3 src/main.py transcribe-file --path /path/to/video.mp4
```

### List all transcripts

```bash
python3 src/main.py list
```

### Get a specific transcript

```bash
python3 src/main.py get --id ABC123
```

## Preferred Download Method (Public Reels/Posts)

The `python3 src/main.py transcribe --url ...` command tries to log in first and will fail with 2FA. **For public posts, bypass this by downloading with the instaloader CLI directly, then transcribing the local file:**

```bash
# Step 1: Download the video (extract shortcode from URL, prefix with -)
cd ~/.myos/workspace/agents/instagram/transcriber/data/videos
instaloader --no-metadata-json --no-captions --no-profile-pic -- -<SHORTCODE>
# e.g. for https://www.instagram.com/reel/DVcpxXHgZga/  →  -DVcpxXHgZga

# Step 2: Transcribe the downloaded mp4
cd ~/.myos/workspace/agents/instagram/transcriber
python3 src/main.py transcribe-file --path "data/videos/-<SHORTCODE>/<filename>.mp4"
```

The shortcode is the alphanumeric ID in the URL after `/reel/`, `/p/`, or `/tv/`.

## Notes

- Public profiles work without credentials
- For private profiles, set `IG_USERNAME` and `IG_PASSWORD` in `~/.myos/workspace/.env`
- Whisper models: `base` (fast), `small`, `medium`, `large` (most accurate)
- Videos are cached in `data/videos/`, transcripts in `data/transcripts/`
- `src/main.py` requires Python 3.10+ (`str | None` syntax) — on older Python, type hints are stripped to bare `=None` defaults
