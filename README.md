# Instagram Agent

A local CLI agent for managing Instagram — read DMs, research profiles, read comments, and post content. Built on [instagrapi](https://github.com/subzeroid/instagrapi), the same private API the mobile app uses.

## What it does

- Read DM threads (inbox or one-on-one)
- Send DMs
- Fetch any public profile: bio, follower count, recent post captions
- Read and reply to comments on posts and Reels
- Post to Feed, Stories, Reels, and carousels
- Resolve handles to user IDs
- Transcribe Instagram videos (via `transcriber/` sub-agent)

## How to use it (and how not to)

**Good uses:**
- Reading inbound DMs and capturing leads into a CRM
- Researching profiles before outreach
- Posting your own content
- Reading comments on your posts

**Do not use it for:**
- Mass DMs or any bulk outreach — this will get your account flagged fast
- Following/unfollowing at scale
- Any automated sending that isn't triggered by a real intent

Instagram monitors behavioral patterns. This agent includes safety guardrails, but they do not make it invincible. Use it like a human would: deliberately, for specific tasks, not in a loop.

## Safety guardrails built in

This agent has several layers of protection to reduce the risk of account flags:

**Human timing** — every action waits a realistic random interval before executing:
- Glance (1.5-4s): between quick lookups
- Read (3-8s): opening a thread or post
- Scroll (5-14s): browsing inbox or a profile grid
- Think (6-16s): landing on a profile
- Compose (4-10s): opening the message box
- Post (10-28s): reviewing content before uploading
- Typing delay: scaled to message length at ~40 WPM

**Daily DM cap** — hard limit of 20 outbound DMs per account per day. Counter resets at midnight UTC. Tracked in `data/usage.json`.

**Device fingerprint persistence** — your device profile (hardware ID, user agent) is generated once and stored in `~/.instagram-agent/device.json`. It is reused across sessions so each login looks like the same device.

**Smart session management** — sessions are validated cheaply before reuse, without triggering a full re-login. Login is only attempted when the session has actually expired.

**Specific error handling** — challenge required, rate limit, bad password, and feedback-blocked responses each surface a clear, actionable message instead of silently failing.

## Requirements

Before installing, make sure you have:

| Requirement | Version | How to install |
|---|---|---|
| **Python** | 3.10 or higher | [python.org](https://python.org) or `brew install python@3.11` |
| **Node.js** | 16 or higher | [nodejs.org](https://nodejs.org) |
| **Git** | any | [git-scm.com](https://git-scm.com) |

The installer handles all Python dependencies (instagrapi, python-dotenv) automatically inside a virtual environment.

## Installation

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/josephtandle/instagram-agent/main/install.sh | bash
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/josephtandle/instagram-agent/main/install.ps1 | iex
```

### Manual install

```bash
git clone https://github.com/josephtandle/instagram-agent
cd instagram-agent
node install/install-instagram.js
```

The installer:
1. Checks for Python 3.10+, Node.js, and Git
2. Copies the agent to `~/Tools/Instagram/` (configurable with `--target /your/path`)
3. Creates a Python virtual environment and installs all dependencies
4. Saves the install path to `~/.instagram-agent/install.json`
5. Registers the `instagram` CLI command globally

## First-time setup

**1. Set your credentials**

Edit `~/.instagram-agent/.env` (created by the installer):

```
IG_USERNAME=your-instagram-handle
IG_PASSWORD=your-password
```

**2. Log in and save your session**

```bash
instagram login
```

If your account has 2FA enabled, you will be prompted for the code. The session is saved and reused automatically — you will not need to log in again unless the session expires.

## Usage

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
instagram send-dm @someuser --text "Hey, thanks for reaching out."
```

Note: limited to 20 DMs per day per account.

### Fetch a profile

```bash
instagram get-profile @someuser --posts 12
```

### Post a Story

```bash
instagram post-story --path /path/to/video.mp4
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
instagram post-carousel --paths /path/one.jpg /path/two.jpg /path/three.jpg --caption "Caption"
```

### Read comments on a post or Reel

```bash
instagram read-comments https://www.instagram.com/p/SHORTCODE/ --limit 20
```

### Reply to a comment

```bash
instagram reply-comment https://www.instagram.com/p/SHORTCODE/ --comment-id 12345678901234567 --text "Thanks!"
```

### Post a top-level comment

```bash
instagram reply-comment SHORTCODE --text "Thanks for watching."
```

### Resolve username to user ID

```bash
instagram resolve-user @someuser @anotheruser
```

### Check agent status

```bash
instagram status
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `IG_USERNAME` | Yes | Your Instagram handle (set in `~/.instagram-agent/.env`) |
| `IG_PASSWORD` | Yes (first login) | Instagram password |
| `INSTAGRAM_AGENT_ENV` | No | Custom path to a `.env` file |

The agent looks for credentials in this order:

1. `INSTAGRAM_AGENT_ENV` (if set — points to a custom `.env` path)
2. `<install-dir>/.env`
3. `~/.instagram-agent/.env`

## Notes

- Uses instagrapi (unofficial private API) — same as the mobile app
- Session is cached after first login; password is only needed once
- Supports video (.mp4, .mov) and image (.jpg, .png) for all post types
- Story tagging places user mentions at evenly distributed positions across the frame
- Good fit for: reading conversations, capturing inbound leads, organizing follow-up, and moving opportunities into a CRM

## Sub-agents

- **`transcriber/`** — Download and transcribe Instagram videos locally using Whisper

## License

MIT
