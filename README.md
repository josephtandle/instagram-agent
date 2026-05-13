# Instagram

Instagram is a local agent for reading DMs, researching profiles, reading comments, posting content, and supporting CRM lead capture from Instagram.

It uses `instagrapi`, which is an unofficial private API. That means it is powerful and useful for early workflows, but it is not the ideal long-term production architecture.

Recommended framing:
- Use it primarily for reading, reviewing, and capturing inbound interest into a CRM.
- Use sending very minimally if at all.
- Do not use it for mass DMs.
- The stronger long-term path is the official Meta API.

## What it can do

- `get-profile`
- `read-dms`
- `send-dm`
- `post-feed`
- `post-story`
- `post-reel`
- `post-carousel`
- `read-comments`
- `reply-comment`
- `resolve-user`

## Sub-agents

- **`transcriber/`** — Download and transcribe Instagram videos using Whisper

## Installation

### One-line install

macOS/Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/josephtandle/instagram-agent/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/josephtandle/instagram-agent/main/install.ps1 | iex
```

### Manual install

```bash
git clone https://github.com/josephtandle/instagram-agent
cd instagram-agent
node install/install-instagram.js --target ~/Tools/Instagram
```

The installer:
- creates a Python virtualenv
- installs dependencies
- saves the resolved absolute install path in `~/.instagram-agent/install.json`
- tries to make the `instagram` command available globally

## Usage

### Authenticate (first time)

```bash
instagram login
```

Session is saved to `data/sessions/<username>.json` and reused automatically.

### Post a Story (with user tags)

```bash
instagram post-story --path /path/to/video.mp4 --tag @handle1 @handle2
```

### Post to Feed

```bash
instagram post-feed --path /path/to/video.mp4 --caption "Caption text"
```

### Post a Reel

```bash
instagram post-reel --path /path/to/video.mp4 --caption "Caption text"
```

### Post a carousel

```bash
instagram post-carousel --paths /path/one.jpg /path/two.jpg --caption "Caption text"
```

### Read recent DM threads

```bash
instagram read-dms --limit 20
```

### Read one specific thread

```bash
instagram read-dms --handle @someuser --limit 50
```

### Send a DM

```bash
instagram send-dm @someuser --text "Hello"
```

### Read comments on a post or Reel

```bash
instagram read-comments https://www.instagram.com/p/SHORTCODE/ --limit 20
```

### Reply to a specific comment

```bash
instagram reply-comment https://www.instagram.com/p/SHORTCODE/ --comment-id 12345678901234567 --text "Appreciate you."
```

### Post a top-level comment

```bash
instagram reply-comment SHORTCODE --text "Thanks for watching."
```

### Resolve username → user ID

```bash
instagram resolve-user @joe.che.official @thedanholloway
```

### Check agent status

```bash
instagram status
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `IG_USERNAME` | No | Default Instagram account (default: joe.che.official) |
| `IG_PASSWORD` | Yes (first login) | Instagram password |

The agent looks for environment variables in this order:

1. `INSTAGRAM_AGENT_ENV` if set
2. `<install-dir>/.env`
3. `~/.instagram-agent/.env`
4. `~/.myos/workspace/.env` for backwards compatibility

## Notes

- Uses instagrapi (unofficial private API) — same as the mobile app
- Session is cached after first login; password only needed once
- Story tagging uses usertag positions spread across the frame
- Supports video (.mp4, .mov) and image (.jpg, .png) for all post types
- Supports reading comments and replying to comments on posts/Reels
- Best use case for Mastermind students: read Instagram conversations, capture leads, organize follow-up, and move opportunities into a CRM
