import { MCPServer } from "mcp-use";
import { z } from "zod";

import { analyseOnBackend, type BackendReport } from "./backend.js";

const server = new MCPServer({
  name: "reel-breakdown",
  title: "Reel Breakdown",
  version: "1.0.0",
  description:
    "Visual edit breakdowns of public Instagram Reels: frames, shot timeline, transitions, titles and transcript.",
  instructions:
    "Use analyse-reel when the user shares a public Instagram Reel link and wants to understand its visuals, " +
    "editing, transitions, animation or script. Only public reels work; private ones are refused.",
  websiteUrl: "https://github.com/Hemanth-hexo/instagram-reel-mcp",
  icons: [{ src: "icon.svg", mimeType: "image/svg+xml", sizes: ["512x512"] }],
});

const analyseInputSchema = z.object({
  url: z
    .string()
    .describe("Public Instagram Reel link, e.g. https://www.instagram.com/reel/ABC123/"),
  transcribe: z
    .boolean()
    .optional()
    .describe("Transcribe speech in the audio (default true; slower)."),
});

const analyseOutputSchema = z.object({
  reel: z.object({
    url: z.string().describe("Canonical reel URL"),
    shortcode: z.string().nullable(),
    username: z.string().nullable().describe("Creator's Instagram handle"),
    caption: z.string().nullable(),
    postedAt: z.string().nullable().describe("ISO date the reel was posted"),
    likes: z.number().nullable(),
    comments: z.number().nullable(),
    views: z.number().nullable(),
  }),
  video: z.object({
    durationS: z.number(),
    width: z.number(),
    height: z.number(),
    aspectRatio: z.string().nullable(),
    fps: z.number().nullable(),
    hasAudio: z.boolean(),
  }),
  pace: z.object({
    shotCount: z.number(),
    transitionCount: z.number(),
    inShotChangeCount: z.number(),
    avgShotS: z.number(),
    cutsPer10s: z.number(),
    label: z.string().describe("Pacing label, e.g. 'very fast (rapid cutting)'"),
    overallMotion: z.string(),
  }),
  shots: z.array(
    z.object({
      index: z.number(),
      startS: z.number(),
      endS: z.number(),
      durationS: z.number(),
      motion: z.string().describe("static | low | medium | high"),
      cameraMove: z.string().nullable(),
      inShotChanges: z.number(),
    })
  ),
  transitions: z.array(
    z.object({
      atS: z.number(),
      type: z
        .string()
        .describe("hard_cut, dissolve, fade_*, flash_white, dip_to_black, motion_cut or whip_or_wipe"),
      durationS: z.number(),
    })
  ),
  inShotChanges: z
    .array(z.object({ atS: z.number(), shareOfFrameChanged: z.number() }))
    .describe("Moments where part of the picture switches while the framing stays (titles, overlays, effects, panels)"),
  transcript: z.object({
    available: z.boolean(),
    speechDetected: z.boolean(),
    language: z.string().nullable(),
    segments: z.array(z.object({ startS: z.number(), endS: z.number(), text: z.string() })),
    note: z.string().nullable(),
  }),
  frames: z
    .array(z.object({ timeS: z.number(), shot: z.number().nullable() }))
    .describe("Timestamps of the key frames, in the same order as the image content blocks"),
});

type Analysis = z.infer<typeof analyseOutputSchema>;

function toAnalysis(r: BackendReport, requestedUrl: string): Analysis {
  const meta = r.metadata ?? {};
  const tech = r.technical;
  const ed = r.editing;
  const tr = r.audio?.transcript ?? {};
  return {
    reel: {
      url: meta.url ?? requestedUrl,
      shortcode: meta.shortcode ?? null,
      username: meta.username ?? null,
      caption: meta.caption ?? null,
      postedAt: meta.upload_timestamp ? new Date(meta.upload_timestamp * 1000).toISOString() : null,
      likes: meta.like_count ?? null,
      comments: meta.comment_count ?? null,
      views: meta.view_count ?? null,
    },
    video: {
      durationS: tech.duration_s,
      width: tech.video.width,
      height: tech.video.height,
      aspectRatio: tech.video.aspect_ratio ?? null,
      fps: tech.video.fps ?? null,
      hasAudio: tech.audio != null,
    },
    pace: {
      shotCount: ed.pace.shot_count ?? ed.shots.length,
      transitionCount: ed.pace.transition_count ?? ed.transitions.length,
      inShotChangeCount: ed.pace.in_shot_change_count ?? (ed.in_shot_changes ?? []).length,
      avgShotS: ed.pace.avg_shot_s ?? tech.duration_s,
      cutsPer10s: ed.pace.cuts_per_10s ?? 0,
      label: ed.pace.pace ?? "unknown",
      overallMotion: ed.pace.overall_motion ?? "unknown",
    },
    shots: ed.shots.map((s) => ({
      index: s.index,
      startS: s.start_s,
      endS: s.end_s,
      durationS: s.duration_s,
      motion: s.motion,
      cameraMove: s.camera_move ?? null,
      inShotChanges: s.in_shot_changes ?? 0,
    })),
    transitions: ed.transitions.map((t) => ({ atS: t.at_s, type: t.type, durationS: t.duration_s })),
    inShotChanges: (ed.in_shot_changes ?? []).map((c) => ({
      atS: c.at_s,
      shareOfFrameChanged: c.share_of_frame_changed,
    })),
    transcript: {
      available: Boolean(tr.available),
      speechDetected: Boolean(tr.speech_detected),
      language: tr.language ?? null,
      segments: (tr.segments ?? []).map((s) => ({ startS: s.start_s, endS: s.end_s, text: s.text })),
      note: tr.note ?? null,
    },
    frames: (r.frames ?? []).map((f) => ({ timeS: f.time_s, shot: f.shot ?? null })),
  };
}

export const analyseReel = server.tool(
  {
    name: "analyse-reel",
    title: "Analyse Instagram Reel",
    description:
      "Break down a public Instagram Reel: key frames, a shot-by-shot edit timeline (cuts, dissolves, fades, " +
      "whip/wipe transitions), in-shot changes such as title animation, overlays and split-screen panels, " +
      "pacing, camera motion and a timestamped transcript. Renders an interactive breakdown card. " +
      "Private or login-walled reels are refused.",
    inputSchema: analyseInputSchema,
    outputSchema: analyseOutputSchema,
    view: {
      name: "reel-breakdown",
      description: "Interactive edit breakdown: filmstrip, shot timeline, transitions and transcript",
      prefersBorder: true,
    },
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
  },
  async ({ url, transcribe }, ctx) => {
    await ctx.reportProgress(0, 100, "Fetching and analysing the reel");
    const result = await analyseOnBackend(url, transcribe ?? true);
    if (!result.ok) {
      return { isError: true, content: [{ type: "text", text: result.message }] };
    }
    return {
      // The model gets the full text report plus the frames; the View renders structuredContent and the frames.
      content: [{ type: "text", text: result.text }, ...result.images],
      structuredContent: toAnalysis(result.report, url),
    };
  }
);

export default server;
