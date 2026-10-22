# Reel Breakdown (MCP App)

An [mcp-use](https://docs.mcp-use.com) v2 MCP App. Its `analyse-reel` tool takes a public Instagram Reel
link and renders an interactive breakdown card in ChatGPT and Claude. The card shows:

- creator and stats
- a frame filmstrip
- a clickable edit timeline: shots shaded by motion, transitions (cuts, dissolves, fades, whip/wipes) and
  in-shot changes (titles, overlays, split-screen panels)
- the transcript
- an "Explain this edit" follow-up button

The analysis itself is done by the Python reel analyser in the parent folder. This app calls it over
JSON-RPC (see [backend.ts](backend.ts)), so this server stays a light Node app.

## Run locally

```bash
npm install
echo 'REEL_ANALYZER_URL=https://<analyser-host>/mcp/<secret>' > .env
npm run dev          # MCP endpoint at http://localhost:3000/mcp, Inspector at /mcp/inspector
```

`REEL_ANALYZER_URL` is the analyser's full MCP URL, including its secret path. It stays on the server and is
never sent to the view.

## Deploy (Manufact Cloud)

```bash
npx -y mcp-use@latest deploy --no-github -y --env-file .env
```

The managed upload never includes `.env*` files. The variable is stored as a server environment variable.
Manage it later with `npx mcp-use servers env set <server> REEL_ANALYZER_URL=...`.

The deployed MCP endpoint has no authentication. Add OAuth (`mcp-use/oauth/*`) before sharing it widely,
because every call runs through the analyser.
