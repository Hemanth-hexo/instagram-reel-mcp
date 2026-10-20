# Instagram Reel MCP server

A small [Model Context Protocol](https://modelcontextprotocol.io) server that lets Claude, ChatGPT and other
MCP clients look at **public** Instagram Reels from a link. It is written in Python on the official MCP SDK
(v2) and served over **Streamable HTTP**.

| Tool | What it returns |
|---|---|
| `inspect_reel(url)` | Creator, caption, posted date, view/like/comment counts, duration, resolution, aspect ratio, fps, codecs, file size, and a poster frame |
| `analyse_reel(url)` | Key frames (images), a timestamped transcript, and measured edit signals: shots, cut times, transitions (hard cut, dissolve, fade, flash, whip/motion cut), pacing, motion level, and camera pans/tilts. It also returns a guide that tells the model how to explain the visuals, editing, animation and transitions |

Both tools also accept `video_file` (an uploaded video) as a fallback when Instagram won't serve the reel.

## Public reels only

| Access method | What it gives | When it is used |
|---|---|---|
| **Anonymous public fetch** (yt-dlp, no login) | The video file and public metadata | Default. Gets only what Instagram serves to a logged-out browser. It never sends cookies, passwords or session tokens, so **private reels are refused** with `not_public` |
| **Official Meta oEmbed** (Graph API) | Metadata only (author, title, thumbnail). It also confirms that a post is public | When `REEL_META_ACCESS_TOKEN` is set |
| **User-supplied file** | Anything you upload yourself | When you pass `video_file` |

The server never logs in and never bypasses Instagram's login wall, age gates or other access controls.
When Instagram refuses an anonymous request, the tool says so and tells the model to ask for an upload.

> **Terms of use.** Meta's official APIs don't provide video files for other accounts' reels, so the
> anonymous public fetch is the only way to get the video from a link. Meta's terms restrict automated
> collection, and reels are often copyrighted. Use this for your own content or for content you have the
> right to analyse. Set `REEL_PUBLIC_FETCH=0` to allow only uploads and official metadata.

## Quick start

Requirements: Python 3.10 or newer, and `ffmpeg` (macOS: `brew install ffmpeg`; Debian/Ubuntu: `apt install ffmpeg`).

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[transcribe]"     # drop [transcribe] to skip speech-to-text
python -m reel_mcp doctor          # checks ffmpeg, yt-dlp and transcription
python -m reel_mcp warmup          # optional: download the speech model now (~140 MB for "base")
python -m reel_mcp serve
```

The MCP endpoint is **`http://127.0.0.1:8000/mcp`** (`reel-mcp serve` works too once the package is installed).
To check it with the MCP Inspector, run `npx @modelcontextprotocol/inspector`, choose *Streamable HTTP*,
and paste the URL.

## Connect a client

### Claude Code

```bash
claude mcp add --transport http instagram-reels http://127.0.0.1:8000/mcp
```

If you set `REEL_AUTH_TOKEN`, add `--header "Authorization: Bearer <token>"`.

### Claude Desktop (server on the same machine)

Bridge to the local HTTP server with `mcp-remote` in `claude_desktop_config.json`, then restart Claude Desktop:

```json
{
  "mcpServers": {
    "instagram-reels": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://127.0.0.1:8000/mcp"]
    }
  }
}
```

If you set a token, append `"--header", "Authorization: Bearer <token>"` to `args`.

### claude.ai, Claude custom connectors and ChatGPT (need a public HTTPS URL)

These clients connect from the provider's cloud, so the server must be reachable over HTTPS.
For a quick test, use a tunnel. For anything permanent, deploy it (see below).

1. Start a tunnel, for example `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`, and note its hostname.
2. These connector UIs can't send a static bearer header, so use a long random endpoint path as the secret.
   The upload page sits under the same path, so it is protected too:

   ```bash
   export REEL_MCP_PATH=/mcp/$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
   export REEL_ALLOWED_HOSTS=<tunnel-hostname>     # otherwise requests get "421 Invalid Host header"
   python -m reel_mcp serve
   ```

3. Add the connector with the URL `https://<tunnel-hostname><REEL_MCP_PATH>` and no authentication:
   - **claude.ai / Claude Desktop:** Settings → Connectors → *Add custom connector*.
   - **ChatGPT:** turn on *Developer mode* (Settings → Apps & Connectors → Advanced), then create a connector.
     Menu names change, so check OpenAI's current docs if they differ.

**ChatGPT and images:** at least one user has reported that ChatGPT drops image blocks returned by MCP tools.
If that happens, ChatGPT still gets the full text report: edit timeline, camera motion, transcript and
metadata. Ask it to call `analyse_reel` with `include_frames: false` to skip the images.

## Fallback: upload the video yourself

If a reel is blocked (rate limit, login wall, or a private account you have access to), use your own copy:

- **Browser:** open `<MCP URL>/upload`, e.g. `http://127.0.0.1:8000/mcp/upload`, choose the file, and press *Upload*.
- **curl:** `curl -T reel.mp4 http://127.0.0.1:8000/mcp/upload/` (add `-H "Authorization: Bearer <token>"` if needed).
- **Folder:** copy the file into `data/inbox/`.

Each upload returns a name like `3fa2c1d0-reel.mp4`. Tell the assistant to run `analyse_reel` with
`video_file="3fa2c1d0-reel.mp4"`. Accepted types are mp4, mov, m4v, webm and mkv. Uploads are checked
with ffprobe and deleted after `REEL_UPLOAD_TTL_S` (24 h by default).

## Tool reference

`inspect_reel(url?, video_file?)` downloads the reel into a cache (reused for an hour, so a follow-up
`analyse_reel` is instant) and returns metadata, technical details and one poster frame.

`analyse_reel(url?, video_file?, max_frames=8, frame_width=512, transcribe=true, include_frames=true)`:

- **Frames:** one per shot. Spare frames go to long shots so movement inside a shot is visible. Each frame is labelled with its time and shot.
- **Edit signals:** measured at up to 30 fps on downscaled frames.
  - *hard_cut*: a one-frame jump.
  - *dissolve*: a blend between two different shots.
  - *fade_to/from_black*, *fade_white*.
  - *flash_white* and *dip_to_black*: one to three frames.
  - *motion_cut*: a cut inside a burst of motion (whip pan, swipe, zoom-blur).
  - Per shot: motion level (static, low, medium, high) and camera pans/tilts from phase correlation.
  - Pace: average and median shot length, transitions per 10 s, and whether the cutting speeds up or slows down.
- **Audio:** loudness, silence, and a local [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
  transcript with timestamps. Music-only reels report "no clear speech".

Errors come back as tool errors with a stable code and a next step: `not_public`, `not_found`,
`invalid_url`, `too_long`, `too_large`, `public_fetch_disabled`, `file_not_found`, `bad_file`,
`missing_input` and `network_error`.

**Accuracy.** Cut detection was frame-exact on synthetic clips and on real Instagram footage spliced with
known cuts. Dissolve timing was within about 0.2 s. Camera moves and transition types are heuristics, so
the model is told to check them against the frames. Creative transitions such as masks and match cuts may
be reported as hard cuts.

## Configuration

Settings come from environment variables or a `.env` file in the working directory.
See [.env.example](.env.example) for the full list.

| Variable | Default | Purpose |
|---|---|---|
| `REEL_HOST` / `REEL_PORT` | `127.0.0.1` / `8000` | Bind address |
| `REEL_MCP_PATH` | `/mcp` | Endpoint path; a random path works as a shared secret |
| `REEL_AUTH_TOKEN` | unset | Require `Authorization: Bearer …` on every request except `/healthz` and the upload page |
| `REEL_ALLOWED_HOSTS` | unset | Extra `Host` headers to accept (tunnel or domain), or `*` to turn off the check behind a trusted proxy |
| `REEL_PUBLIC_FETCH` | `1` | Anonymous public download on or off |
| `REEL_META_ACCESS_TOKEN` | unset | `<app-id>\|<client-token>` for official oEmbed metadata |
| `REEL_DATA_DIR` | `data` | Cache, uploads (`inbox/`) |
| `REEL_MAX_DURATION_S` / `REEL_MAX_VIDEO_MB` / `REEL_MAX_UPLOAD_MB` | `300` / `150` / `200` | Limits |
| `REEL_WHISPER_MODEL` | `base` | `tiny`, `base`, `small`, `medium` or `large-v3` |

## Deploy

### Vercel (always-on link)

The repo includes [Dockerfile.vercel](Dockerfile.vercel). Vercel's
[Container Runtime](https://vercel.com/docs/functions/runtimes/container) (beta, all plans) detects it and
runs the server as a container function, so it doesn't use Vercel's Python runtime. The image has
`ffmpeg` and the speech model built in.

1. Import the GitHub repo in Vercel (or run `vercel link` in this folder).
2. Add a Production environment variable `REEL_MCP_PATH` set to `/mcp/<long-random-string>`. This is the
   secret part of the link. Save it, because a Secret-type variable can't be viewed again.
3. Push to `main` (or run `vercel deploy --prod`). Each push redeploys.
4. Connect your clients to `https://<project>.vercel.app/mcp/<long-random-string>`.

Vercel limits that apply to this server:

- Each tool call can run for at most 300 s on Hobby, with 2 GB of memory. Reels take a few seconds.
- Uploads are capped at 4 MB, because Vercel request bodies max out at 4.5 MB. An upload is kept on one
  instance, so if Vercel answers the next call from another instance the file isn't found. Uploads are
  more reliable when you run the server locally.
- After 5 idle minutes the server scales to zero, so the next call has a cold start.
- When tested in October 2026, Instagram served public reels to Vercel's servers. It can start refusing
  datacenter IPs at any time, though. You'd then get `not_public` even for public reels, and the upload
  fallback is the workaround.
- Vercel's default deployment protection puts a Vercel login in front of preview and per-deployment URLs.
  Use the project's production domain (`<project>.vercel.app`), which is public. Only the secret path
  guards it.

### Docker anywhere else

```bash
docker build -t reel-mcp .
docker run -d -p 8000:8000 -v reel-data:/data \
  -e REEL_MCP_PATH=/mcp/<long-random-string> -e REEL_ALLOWED_HOSTS=reels.example.com \
  reel-mcp
```

Put it behind HTTPS (Caddy, nginx, Fly.io, Render, Cloud Run and so on). The server is stateless
(Streamable HTTP in stateless mode), so restarts and multiple replicas are fine. The speech model downloads
into the `/data` volume on first use. Be aware that Instagram often refuses anonymous requests from
datacenter IPs, so a cloud deployment may rely more on uploads than a home machine does.

**Security defaults:**
- It binds to localhost.
- URLs are limited to `instagram.com` reel, post and tv links, so the server can't be used to fetch arbitrary hosts.
- `video_file` must be a bare name inside the inbox, so no other path can be read.
- Uploads are size-capped and probed.
- No credentials are ever sent to Instagram.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `not_public` for a reel you can open while logged out | Instagram is rate-limiting your IP. Wait, try from another network, run `pip install -U yt-dlp`, or upload the file |
| `421 Invalid Host header` | Add the hostname to `REEL_ALLOWED_HOSTS` |
| `401 unauthorized` | Send `Authorization: Bearer <REEL_AUTH_TOKEN>` |
| Transcript says "Transcription disabled" | `pip install 'reel-mcp[transcribe]'` |
| First `analyse_reel` is slow | The speech model is downloading; run `python -m reel_mcp warmup` once |

## Development

```bash
pip install -e ".[dev,transcribe]"
python -m pytest                                  # unit + end-to-end over Streamable HTTP
REEL_TEST_TRANSCRIBE=1 python -m pytest           # also run real speech-to-text (macOS `say` voice)
REEL_LIVE_URL=https://www.instagram.com/reel/<code>/ python -m pytest   # also hit Instagram
```

```
src/reel_mcp/
  server.py        MCP tools, upload route, bearer auth, Streamable HTTP app
  sources.py       picks the access method: user file → official oEmbed → public fetch
  providers/       instagram_public.py (yt-dlp, anonymous), meta_oembed.py, user_file.py
  analysis.py      inspect/analyse reports, frame selection, markdown rendering
  editing.py       cut/transition/motion/pacing detection (numpy)
  media.py         ffmpeg/ffprobe wrappers
  transcribe.py    faster-whisper
  urls.py errors.py config.py cli.py
```
