/** Reel Breakdown view: filmstrip, shot timeline, transitions, in-shot changes and transcript. */
import {
  ModelContext,
  ThemeProvider,
  useHostContext,
  useOpenExternal,
  useSendFollowUp,
  useToolContext,
} from "mcp-use/react";
import { useMemo, useState } from "react";

import "./view.css";

const TRANSITION_STYLE: Record<string, { label: string; dot: string }> = {
  hard_cut: { label: "Cut", dot: "bg-zinc-900 dark:bg-zinc-100" },
  motion_cut: { label: "Motion cut", dot: "bg-orange-500" },
  whip_or_wipe: { label: "Whip / wipe", dot: "bg-fuchsia-500" },
  dissolve: { label: "Dissolve", dot: "bg-sky-500" },
  fade_to_black: { label: "Fade out", dot: "bg-indigo-500" },
  fade_from_black: { label: "Fade in", dot: "bg-indigo-500" },
  fade_white: { label: "Fade (white)", dot: "bg-indigo-300" },
  flash_white: { label: "Flash", dot: "bg-yellow-400" },
  dip_to_black: { label: "Dip to black", dot: "bg-indigo-700" },
};

const MOTION_FILL: Record<string, string> = {
  static: "bg-pink-100 dark:bg-pink-950",
  low: "bg-pink-200 dark:bg-pink-900",
  medium: "bg-pink-400 dark:bg-pink-700",
  high: "bg-pink-600 dark:bg-pink-500",
};

const fmt = (s: number) => `${s.toFixed(s < 10 ? 2 : 1)}s`;
const compact = (n: number | null) =>
  n == null ? null : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${(n / 1e3).toFixed(1)}K` : `${n}`;

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-full border border-zinc-200 px-2.5 py-1 text-xs text-zinc-700 dark:border-zinc-700 dark:text-zinc-300">
      {children}
    </span>
  );
}

function Skeleton({ url }: { url?: string }) {
  return (
    <div className="space-y-4 p-5">
      <div className="h-6 w-48 animate-pulse rounded bg-zinc-200 dark:bg-zinc-800" />
      <p className="m-0 text-sm text-zinc-500">Analysing {url ?? "the reel"}… this takes 5–20 seconds.</p>
      <div className="h-10 w-full animate-pulse rounded-lg bg-zinc-200 dark:bg-zinc-800" />
      <div className="flex gap-2">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="h-36 w-24 animate-pulse rounded-lg bg-zinc-200 dark:bg-zinc-800" />
        ))}
      </div>
    </div>
  );
}

export default function ReelBreakdown() {
  const view = useToolContext<"analyse-reel">();
  const { hostCapabilities } = useHostContext();
  const openExternal = useOpenExternal();
  const sendFollowUp = useSendFollowUp();
  const [selected, setSelected] = useState(1);
  const [showCaption, setShowCaption] = useState(false);

  const frames = useMemo(() => {
    if (view.status !== "ready") return [];
    const images = (view.content ?? []).filter((b: any) => b.type === "image") as any[];
    return images.map((b, i) => ({
      src: `data:${b.mimeType ?? "image/jpeg"};base64,${b.data}`,
      timeS: view.toolOutput.frames[i]?.timeS ?? 0,
      shot: view.toolOutput.frames[i]?.shot ?? null,
    }));
  }, [view]);

  if (view.status === "pending") {
    return (
      <ThemeProvider>
        <Skeleton url={view.toolInput?.url} />
      </ThemeProvider>
    );
  }
  if (view.status === "error") {
    return (
      <ThemeProvider>
        <div className="p-5">
          <h2 className="m-0 text-base font-semibold text-zinc-900 dark:text-zinc-100">Couldn't analyse this reel</h2>
          <p className="mt-2 mb-0 text-sm text-zinc-600 dark:text-zinc-400">{view.error.message}</p>
        </div>
      </ThemeProvider>
    );
  }

  const a = view.toolOutput;
  const total = Math.max(a.video.durationS, 0.01);
  const shot = a.shots.find((s) => s.index === selected) ?? a.shots[0];
  const into = shot ? a.transitions.find((t) => Math.abs(t.atS + t.durationS - shot.startS) < 0.1) : undefined;
  const changesInShot = shot ? a.inShotChanges.filter((c) => c.atS >= shot.startS && c.atS < shot.endS) : [];
  const canOpen = hostCapabilities?.openLinks !== undefined;
  const canFollowUp = hostCapabilities?.message !== undefined;
  const handle = a.reel.username ? `@${a.reel.username}` : "Reel";
  const stats = [
    a.reel.postedAt && new Date(a.reel.postedAt).toLocaleDateString(),
    compact(a.reel.views) && `${compact(a.reel.views)} views`,
    compact(a.reel.likes) && `${compact(a.reel.likes)} likes`,
    compact(a.reel.comments) && `${compact(a.reel.comments)} comments`,
  ].filter(Boolean);
  const usedTypes = [...new Set(a.transitions.map((t) => t.type))];

  return (
    <ThemeProvider>
      {shot && (
        <ModelContext
          content={`The user is looking at shot ${shot.index} (${fmt(shot.startS)}–${fmt(shot.endS)}, ${shot.motion} motion) in the ${handle} reel breakdown.`}
        />
      )}
      <div className="space-y-5 bg-white p-5 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <div className="ig-ring flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-sm font-semibold text-white">
              {(a.reel.username ?? "R").slice(0, 1).toUpperCase()}
            </div>
            <div className="min-w-0">
              <div className="truncate text-base font-semibold">{handle}</div>
              {stats.length > 0 && <div className="truncate text-xs text-zinc-500">{stats.join(" · ")}</div>}
            </div>
          </div>
          {canOpen && (
            <button
              type="button"
              className="shrink-0 cursor-pointer rounded-full border border-zinc-300 bg-transparent px-3 py-1.5 text-xs text-zinc-800 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
              onClick={() => void openExternal({ url: a.reel.url })}
            >
              Open reel ↗
            </button>
          )}
        </div>

        {a.reel.caption && (
          <button
            type="button"
            className={`w-full cursor-pointer border-0 bg-transparent p-0 text-left text-sm whitespace-pre-line text-zinc-600 dark:text-zinc-400 ${showCaption ? "block" : "line-clamp-2"}`}
            onClick={() => setShowCaption((v) => !v)}
          >
            {a.reel.caption}
          </button>
        )}

        <div className="flex flex-wrap gap-1.5">
          <Chip>{fmt(a.video.durationS)}</Chip>
          {a.video.aspectRatio && <Chip>{a.video.aspectRatio}</Chip>}
          <Chip>{a.pace.shotCount} shots</Chip>
          <Chip>{a.pace.transitionCount} transitions</Chip>
          {a.pace.inShotChangeCount > 0 && <Chip>{a.pace.inShotChangeCount} in-shot changes</Chip>}
          <Chip>pace: {a.pace.label}</Chip>
          <Chip>motion: {a.pace.overallMotion}</Chip>
        </div>

        {/* Timeline */}
        <div>
          <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">Edit timeline</div>
          <div className="relative pt-3">
            {a.inShotChanges.map((c) => (
              <span
                key={`c${c.atS}`}
                title={`${fmt(c.atS)} in-shot change (${Math.round(c.shareOfFrameChanged * 100)}% of frame)`}
                className="absolute top-0 h-2 w-2 -translate-x-1/2 rotate-45 bg-amber-500"
                style={{ left: `${(c.atS / total) * 100}%` }}
              />
            ))}
            <div className="relative flex h-10 overflow-hidden rounded-lg">
              {a.shots.map((s) => (
                <button
                  key={s.index}
                  type="button"
                  title={`Shot ${s.index}: ${fmt(s.startS)}–${fmt(s.endS)}, ${s.motion} motion`}
                  className={`h-full cursor-pointer border-0 border-r border-white p-0 dark:border-zinc-950 ${MOTION_FILL[s.motion] ?? MOTION_FILL.low} ${s.index === shot?.index ? "outline outline-2 -outline-offset-2 outline-zinc-900 dark:outline-white" : ""}`}
                  style={{ left: `${(s.startS / total) * 100}%`, position: "absolute", width: `${(s.durationS / total) * 100}%` }}
                  onClick={() => setSelected(s.index)}
                />
              ))}
              {a.transitions.map((t) => (
                <span
                  key={`t${t.atS}`}
                  title={`${fmt(t.atS)} ${TRANSITION_STYLE[t.type]?.label ?? t.type}`}
                  className={`pointer-events-none absolute top-0 h-full ${TRANSITION_STYLE[t.type]?.dot ?? "bg-zinc-900"}`}
                  style={{ left: `${(t.atS / total) * 100}%`, width: t.durationS > 0 ? `${(t.durationS / total) * 100}%` : "2px", opacity: t.durationS > 0 ? 0.7 : 1 }}
                />
              ))}
            </div>
            <div className="mt-1 flex justify-between text-[10px] tabular-nums text-zinc-500">
              <span>0s</span>
              <span>{fmt(total / 2)}</span>
              <span>{fmt(total)}</span>
            </div>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-zinc-500">
            {usedTypes.map((t) => (
              <span key={t} className="inline-flex items-center gap-1">
                <span className={`inline-block h-2.5 w-1 ${TRANSITION_STYLE[t]?.dot ?? "bg-zinc-900"}`} />
                {TRANSITION_STYLE[t]?.label ?? t}
              </span>
            ))}
            {a.inShotChanges.length > 0 && (
              <span className="inline-flex items-center gap-1">
                <span className="inline-block h-2 w-2 rotate-45 bg-amber-500" /> In-shot change (title, overlay, panel)
              </span>
            )}
            <span>Shade = motion level</span>
          </div>
        </div>

        {/* Selected shot */}
        {shot && (
          <div className="rounded-xl border border-zinc-200 p-3 text-sm dark:border-zinc-800">
            <div className="font-medium">
              Shot {shot.index} of {a.shots.length} · {fmt(shot.startS)}–{fmt(shot.endS)} ({fmt(shot.durationS)})
            </div>
            <div className="mt-1 text-xs text-zinc-500">
              {shot.motion} motion
              {shot.cameraMove ? ` · camera: ${shot.cameraMove}` : ""}
              {into ? ` · enters via ${TRANSITION_STYLE[into.type]?.label.toLowerCase() ?? into.type}` : ""}
              {changesInShot.length > 0
                ? ` · ${changesInShot.length} in-shot change${changesInShot.length > 1 ? "s" : ""} at ${changesInShot.map((c) => fmt(c.atS)).join(", ")}`
                : ""}
            </div>
          </div>
        )}

        {/* Filmstrip */}
        {frames.length > 0 && (
          <div>
            <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">Key frames</div>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {frames.map((f, i) => (
                <button
                  key={i}
                  type="button"
                  className={`shrink-0 cursor-pointer rounded-lg border-2 bg-transparent p-0 ${f.shot === shot?.index ? "border-pink-500" : "border-transparent"}`}
                  onClick={() => f.shot != null && setSelected(f.shot)}
                >
                  <img src={f.src} alt={`Frame at ${fmt(f.timeS)}`} className="block h-36 w-auto rounded-md" />
                  <div className="py-1 text-center text-[10px] tabular-nums text-zinc-500">
                    {fmt(f.timeS)}
                    {f.shot != null ? ` · shot ${f.shot}` : ""}
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Transcript */}
        <div>
          <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">Audio</div>
          {a.transcript.speechDetected ? (
            <ul className="m-0 max-h-48 list-none space-y-1 overflow-y-auto p-0 text-sm">
              {a.transcript.segments.map((s, i) => (
                <li key={i}>
                  <span className="mr-2 tabular-nums text-xs text-zinc-500">{fmt(s.startS)}</span>
                  {s.text}
                </li>
              ))}
            </ul>
          ) : (
            <p className="m-0 text-sm text-zinc-500">
              {!a.video.hasAudio
                ? "No audio track."
                : a.transcript.available
                  ? "No clear speech, so likely music or sound effects only."
                  : (a.transcript.note ?? "No transcript.")}
            </p>
          )}
        </div>

        {canFollowUp && (
          <button
            type="button"
            className="ig-button w-full cursor-pointer rounded-full border-0 px-4 py-2.5 text-sm font-medium text-white"
            onClick={() =>
              void sendFollowUp({
                prompt: `Explain how the ${handle} reel is edited: the hook, shot pacing, each transition, the title and overlay animation, and how the audio drives the cuts.`,
              }).catch(() => {})
            }
          >
            Explain this edit
          </button>
        )}
      </div>
    </ThemeProvider>
  );
}
