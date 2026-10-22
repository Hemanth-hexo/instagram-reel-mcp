/**
 * Client for the reel analyser (the Python MCP server in this repo's parent, deployed on Vercel).
 * Speaks plain JSON-RPC to its stateless Streamable HTTP endpoint, so no extra dependencies are needed.
 *
 * REEL_ANALYZER_URL is the analyser's full MCP URL, including its secret path. It stays on the server.
 */

export interface BackendReport {
  metadata?: {
    url?: string;
    shortcode?: string;
    username?: string;
    caption?: string;
    upload_timestamp?: number;
    like_count?: number;
    comment_count?: number;
    view_count?: number;
  };
  technical: {
    duration_s: number;
    video: { width: number; height: number; aspect_ratio?: string | null; fps?: number | null };
    audio: unknown | null;
  };
  editing: {
    shots: Array<{
      index: number;
      start_s: number;
      end_s: number;
      duration_s: number;
      motion: string;
      camera_move?: string | null;
      in_shot_changes?: number;
    }>;
    transitions: Array<{ at_s: number; type: string; duration_s: number }>;
    in_shot_changes?: Array<{ at_s: number; share_of_frame_changed: number }>;
    pace: {
      shot_count?: number;
      transition_count?: number;
      in_shot_change_count?: number;
      avg_shot_s?: number;
      cuts_per_10s?: number;
      pace?: string;
      overall_motion?: string;
    };
  };
  audio?: {
    transcript?: {
      available?: boolean;
      speech_detected?: boolean;
      language?: string;
      segments?: Array<{ start_s: number; end_s: number; text: string }>;
      note?: string;
    };
  };
  frames?: Array<{ time_s: number; shot?: number | null }>;
}

type ImageBlock = { type: "image"; data: string; mimeType: string };

export type BackendResult =
  | { ok: true; text: string; images: ImageBlock[]; report: BackendReport }
  | { ok: false; message: string };

const TIMEOUT_MS = 240_000;

/** Pull the JSON-RPC response for `id` out of a JSON or SSE body. */
function parseRpc(body: string, id: number): any {
  const trimmed = body.trim();
  if (trimmed.startsWith("{")) return JSON.parse(trimmed);
  for (const line of trimmed.split(/\r?\n/)) {
    if (!line.startsWith("data:")) continue;
    const msg = JSON.parse(line.slice(5).trim());
    if (msg.id === id) return msg;
  }
  throw new Error("No response from the analyser");
}

export async function analyseOnBackend(url: string, transcribe: boolean): Promise<BackendResult> {
  const endpoint = process.env.REEL_ANALYZER_URL;
  if (!endpoint) {
    return { ok: false, message: "Server misconfigured: REEL_ANALYZER_URL is not set." };
  }
  const id = 1;
  let rpc: any;
  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id,
        method: "tools/call",
        params: { name: "analyse_reel", arguments: { url, transcribe } },
      }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!res.ok) {
      return { ok: false, message: `The reel analyser returned HTTP ${res.status}. Try again shortly.` };
    }
    rpc = parseRpc(await res.text(), id);
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    return { ok: false, message: `Could not reach the reel analyser: ${reason}` };
  }
  if (rpc.error) {
    return { ok: false, message: `Reel analyser error: ${rpc.error.message ?? "unknown"}` };
  }
  const result = rpc.result ?? {};
  const blocks: any[] = result.content ?? [];
  const text = blocks.find((b) => b.type === "text")?.text ?? "";
  if (result.isError) {
    // e.g. [not_public] ... with an upload hint that only applies to the analyser's own page.
    return { ok: false, message: text.split("\n")[0] || "The reel could not be analysed." };
  }
  const images: ImageBlock[] = blocks
    .filter((b) => b.type === "image")
    .map((b) => ({ type: "image", data: b.data, mimeType: b.mimeType ?? "image/jpeg" }));
  return { ok: true, text, images, report: result.structuredContent as BackendReport };
}
