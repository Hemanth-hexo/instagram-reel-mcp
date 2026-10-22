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
    .describe(
      "Link to the Instagram Reel to analyse, as shared from the Instagram app or website. " +
        "Only reels from public accounts can be fetched."
    ),
  transcribe: z
    .boolean()
    .default(true)
    .describe(
      "Whether to transcribe speech in the audio. Turn it off for a faster result when the reel is " +
        "music-only or the transcript isn't needed."
    ),
});

const analyseOutputSchema = z.object({
  reel: z
    .object({
      url: z.string().describe("Canonical link to the reel"),
      shortcode: z.string().nullable().describe("Instagram's ID for the reel, taken from its link"),
      username: z.string().nullable().describe("Creator's Instagram handle, without the @"),
      caption: z.string().nullable().describe("The reel's caption text, including hashtags"),
      postedAt: z.string().nullable().describe("When the reel was posted, as an ISO 8601 timestamp"),
      likes: z.number().nullable().describe("Like count when analysed; null if Instagram didn't provide it"),
      comments: z.number().nullable().describe("Comment count when analysed; null if not provided"),
      views: z.number().nullable().describe("View count when analysed; null if not provided"),
    })
    .describe("The reel and its creator, as Instagram shows them to logged-out visitors"),
  video: z
    .object({
      durationS: z.number().describe("Length of the video in seconds"),
      width: z.number().describe("Displayed width in pixels"),
      height: z.number().describe("Displayed height in pixels"),
      aspectRatio: z.string().nullable().describe("Aspect ratio such as 9:16, 4:5 or 4:3"),
      fps: z.number().nullable().describe("Frames per second"),
      hasAudio: z.boolean().describe("Whether the video has an audio track"),
    })
    .describe("Technical details of the video file"),
  pace: z
    .object({
      shotCount: z.number().describe("Number of shots (continuous segments between transitions)"),
      transitionCount: z.number().describe("Number of transitions between shots"),
      inShotChangeCount: z.number().describe("Number of in-shot changes (see inShotChanges)"),
      avgShotS: z.number().describe("Average shot length in seconds"),
      cutsPer10s: z.number().describe("Transitions per 10 seconds of video; higher means faster cutting"),
      label: z.string().describe("Pacing label, e.g. 'very fast (rapid cutting)' or 'single continuous shot'"),
      overallMotion: z.string().describe("Duration-weighted motion level across shots: static, low, medium or high"),
    })
    .describe("How fast the reel is cut and how much it moves"),
  shots: z
    .array(
      z.object({
        index: z.number().describe("Shot number, starting at 1"),
        startS: z.number().describe("Start time in seconds"),
        endS: z.number().describe("End time in seconds"),
        durationS: z.number().describe("Shot length in seconds"),
        motion: z.string().describe("Motion level inside the shot: static, low, medium or high"),
        cameraMove: z.string().nullable().describe("Detected camera move such as 'pan right' or 'tilt up'; null if none"),
        inShotChanges: z.number().describe("Number of in-shot changes inside this shot"),
      })
    )
    .describe("Shots in time order"),
  transitions: z
    .array(
      z.object({
        atS: z.number().describe("Time in seconds where the transition starts"),
        type: z
          .string()
          .describe(
            "hard_cut, dissolve, fade_to_black, fade_from_black, fade_white, flash_white, dip_to_black, " +
              "motion_cut (a cut inside a whip/swipe/zoom-blur) or whip_or_wipe (fast motion or a foreground " +
              "object hiding the cut)"
          ),
        durationS: z.number().describe("Length of the transition in seconds; 0 for a hard cut"),
      })
    )
    .describe("Transitions in time order. Types are measured heuristics; check them against the frames"),
  inShotChanges: z
    .array(
      z.object({
        atS: z.number().describe("Time in seconds"),
        shareOfFrameChanged: z.number().describe("Share of the frame that changed, from 0 to 1"),
      })
    )
    .describe(
      "Moments where part of the picture switches while the framing stays: title or text animation, overlays, " +
        "colour or flash effects, split-screen panels or jump cuts. They don't start a new shot"
    ),
  transcript: z
    .object({
      available: z.boolean().describe("Whether transcription ran"),
      speechDetected: z.boolean().describe("Whether any speech was found; false for music-only reels"),
      language: z.string().nullable().describe("Detected language code, e.g. 'en'"),
      segments: z
        .array(
          z.object({
            startS: z.number().describe("Segment start in seconds"),
            endS: z.number().describe("Segment end in seconds"),
            text: z.string().describe("What was said"),
          })
        )
        .describe("Timestamped speech segments"),
      note: z.string().nullable().describe("Why there is no transcript, when there isn't one"),
    })
    .describe("Speech in the audio"),
  frames: z
    .array(
      z.object({
        timeS: z.number().describe("Time of the frame in seconds"),
        shot: z.number().nullable().describe("Shot number the frame belongs to"),
      })
    )
    .describe("Key frames, in the same order as the image blocks in the result content"),
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
      "Break down how an Instagram Reel is made: key frames, a shot-by-shot edit timeline (cuts, dissolves, " +
      "fades, whip/wipe transitions), in-shot changes such as title animation, overlays and split-screen " +
      "panels, pacing, camera motion and a timestamped transcript. Renders an interactive breakdown card. " +
      "Use it when the user shares a reel and asks about its visuals, editing, animation or script. " +
      "Reels from private accounts can't be fetched and come back as an error.",
    inputSchema: analyseInputSchema,
    outputSchema: analyseOutputSchema,
    view: {
      name: "reel-breakdown",
      description: "Interactive edit breakdown: filmstrip, shot timeline, transitions and transcript",
      // One dedicated HTTPS origin for this app's views (required for ChatGPT submission).
      domain: process.env.REEL_VIEW_DOMAIN ?? "https://warm-zero-abu7c.run.mcp-use.com",
      prefersBorder: true,
    },
    // Status labels while the tool runs / after it finishes. OpenAI documents the flat keys; the nested
    // form is what Manufact's compatibility checker reads.
    _meta: {
      "openai/toolInvocation/invoking": "Analysing reel…",
      "openai/toolInvocation/invoked": "Analysed reel",
      "openai/toolInvocation": { invoking: "Analysing reel…", invoked: "Analysed reel" },
    },
    // Reads public data only; repeating a call has no side effects (counts may have moved on Instagram).
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
  },
  async ({ url, transcribe }, ctx) => {
    await ctx.reportProgress(0, 100, "Fetching and analysing the reel");
    const result = await analyseOnBackend(url, transcribe);
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
