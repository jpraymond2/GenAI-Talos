import { useEffect, useRef, useState } from "react";
import { sendVoice } from "../api";
import {
  MIN_HOLD_MS,
  MIN_RECORDING_BYTES,
  pickRecorderMime,
} from "../recorderMime";
import { speakText, unlockSpeech } from "../speech";
import type { Candidate, Evidence, EvidenceSource, RunState, TraceEvent } from "../types";
import { SourceChip } from "./SourceChip";
import { TraceTimeline } from "./TraceTimeline";

type PhoneStep = "Listening" | "Transcribing" | "Thinking" | "Ready";

interface Props {
  run: RunState | null;
  events: TraceEvent[];
  busy: boolean;
  onAsk: (text: string, opts?: { runId?: string }) => Promise<void>;
  onVoiceState: (state: RunState) => void;
  onPickCandidate: (c: Candidate) => void;
  onConfirmActions: () => void;
  onCancelActions: () => void;
}

function isEvidenceSource(value: string): value is EvidenceSource {
  return (
    value === "gmail" ||
    value === "imessage" ||
    value === "whatsapp" ||
    value === "calendar" ||
    value === "web" ||
    value === "memory" ||
    value === "linkedin"
  );
}

function deriveStep(
  recording: boolean,
  thinking: boolean,
  transcript: string | null,
  run: RunState | null,
  events: TraceEvent[],
): PhoneStep {
  if (recording) return "Listening";
  if (thinking) {
    const hasRouter = events.some(
      (e) =>
        e.agent === "router" ||
        e.kind === "intent" ||
        (typeof e.payload?.person_name === "string" && e.payload.person_name),
    );
    if (!transcript && !hasRouter) return "Transcribing";
    return "Thinking";
  }
  if (run?.status === "done" || run?.status === "needs_clarification" || run?.status === "awaiting_confirmation") {
    return "Ready";
  }
  return "Ready";
}

function formatTimer(ms: number): string {
  const s = Math.floor(ms / 1000);
  const tenths = Math.floor((ms % 1000) / 100);
  return `${s}.${tenths}s`;
}

export function PhoneView({
  run,
  events,
  busy,
  onAsk,
  onVoiceState,
  onPickCandidate,
  onConfirmActions,
  onCancelActions,
}: Props) {
  const [recording, setRecording] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [traceOpen, setTraceOpen] = useState(false);
  const [showTranscript, setShowTranscript] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const [liveTranscript, setLiveTranscript] = useState<string | null>(null);
  const [statusLine, setStatusLine] = useState<string | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [expandedChip, setExpandedChip] = useState<string | null>(null);

  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const holdingRef = useRef(false);
  const holdStartedAtRef = useRef(0);
  const talkBtnRef = useRef<HTMLButtonElement | null>(null);
  const pulledNameRef = useRef<string | null>(null);

  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((t) => t.stop());
    };
  }, []);

  useEffect(() => {
    const el = talkBtnRef.current;
    if (!el) return;
    const onTouchStart = (e: TouchEvent) => {
      e.preventDefault();
    };
    const onContextMenu = (e: Event) => {
      e.preventDefault();
    };
    el.addEventListener("touchstart", onTouchStart, { passive: false });
    el.addEventListener("contextmenu", onContextMenu);
    return () => {
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("contextmenu", onContextMenu);
    };
  }, []);

  useEffect(() => {
    if (!recording) {
      setElapsedMs(0);
      return;
    }
    const started = holdStartedAtRef.current || performance.now();
    const id = window.setInterval(() => {
      setElapsedMs(performance.now() - started);
    }, 100);
    return () => window.clearInterval(id);
  }, [recording]);

  // Trace-driven "Pulling up <name>…" once the router resolves a person.
  useEffect(() => {
    if (!thinking) return;
    const name =
      run?.entity_name ||
      (typeof events.find((e) => typeof e.payload?.person_name === "string")?.payload
        ?.person_name === "string"
        ? String(
            events.find((e) => typeof e.payload?.person_name === "string")?.payload
              ?.person_name,
          )
        : null) ||
      run?.intent?.person_name ||
      null;
    if (!name || pulledNameRef.current === name) return;
    pulledNameRef.current = name;
    setStatusLine(`Pulling up ${name}…`);
    speakText(`Pulling up ${name}`);
  }, [thinking, run, events]);

  async function startHold() {
    if (busy || thinking || holdingRef.current) return;
    holdingRef.current = true;
    holdStartedAtRef.current = performance.now();
    unlockSpeech();
    setHint(null);
    setLiveTranscript(null);
    setStatusLine(null);
    setExpandedChip(null);
    pulledNameRef.current = null;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime = pickRecorderMime();
      const recorder = new MediaRecorder(stream, { mimeType: mime });
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        chunksRef.current.push(e.data);
      };
      mediaRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      holdingRef.current = false;
      holdStartedAtRef.current = 0;
      setHint("Microphone permission denied.");
    }
  }

  async function endHold() {
    if (!holdingRef.current) return;
    holdingRef.current = false;
    setRecording(false);
    const heldMs = performance.now() - holdStartedAtRef.current;
    holdStartedAtRef.current = 0;
    const recorder = mediaRef.current;
    mediaRef.current = null;
    if (!recorder) return;

    // Perceived latency: acknowledge inside the gesture chain (iOS).
    unlockSpeech();
    speakText("One sec");

    const blob = await new Promise<Blob>((resolve) => {
      recorder.onstop = () => {
        resolve(
          new Blob(chunksRef.current, {
            type: recorder.mimeType || pickRecorderMime(),
          }),
        );
      };
      try {
        recorder.stop();
      } catch {
        resolve(new Blob());
      }
    });
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    chunksRef.current = [];

    if (heldMs < MIN_HOLD_MS || blob.size < MIN_RECORDING_BYTES) {
      setHint("Hold a bit longer");
      return;
    }

    setThinking(true);
    setStatusLine("Transcribing…");
    const continueId =
      run?.status === "needs_clarification" || run?.status === "awaiting_confirmation"
        ? run.run_id
        : undefined;

    try {
      const outcome = await sendVoice(blob, { runId: continueId });
      if (!outcome.ok) {
        setHint(outcome.error);
        return;
      }
      const result = outcome.data;
      if (result.transcript) {
        setLiveTranscript(result.transcript);
        setStatusLine("Thinking…");
      }

      // Speak headline immediately — don't wait for React paint.
      const headline =
        result.final_brief?.headline ||
        (result.speech ? result.speech.split(/[.!?]/)[0] : "") ||
        result.question ||
        "";
      if (headline) speakText(headline);
      else if (result.speech) speakText(result.speech);

      if (result.run_id) {
        onVoiceState(result as RunState);
      } else if (result.transcript) {
        await onAsk(result.transcript, { runId: continueId });
      }
    } finally {
      setThinking(false);
      setStatusLine(null);
    }
  }

  const brief = run?.final_brief ?? run?.drafts?.at(-1) ?? null;
  const step = deriveStep(recording, thinking, liveTranscript, run, events);
  const contextConflicts = (run?.context as { conflicts?: unknown[] } | undefined)
    ?.conflicts;
  const conflictField = Boolean(
    (Array.isArray(contextConflicts) && contextConflicts.length > 0) ||
      run?.verdicts?.some((v) =>
        v.verdicts?.some((p) => p.decision === "conflict"),
      ),
  );

  return (
    <div
      className="mx-auto flex min-h-[100dvh] max-w-lg flex-col bg-white text-ink-950"
      style={{ paddingTop: "env(safe-area-inset-top, 0px)" }}
    >
      <header className="sticky top-0 z-20 flex items-center justify-between bg-white px-4 pb-3 pt-3">
        <p className="font-display text-2xl font-semibold tracking-tight text-ink-950 opacity-100">
          Recall
        </p>
        <div className="flex items-center gap-2 text-xs font-medium text-ink-500">
          <span className="inline-block h-2 w-2 rounded-full bg-sea-500" aria-hidden />
          Connected
        </div>
      </header>

      <div className="flex items-center gap-2 px-4 pb-2">
        {(["Listening", "Transcribing", "Thinking", "Ready"] as PhoneStep[]).map((s) => (
          <span
            key={s}
            className={`rounded-md px-2 py-1 text-[11px] font-semibold uppercase tracking-wide ${
              step === s
                ? "bg-ink-900 text-white"
                : "bg-ink-100 text-ink-400"
            }`}
          >
            {s}
          </span>
        ))}
      </div>

      <div
        className="flex-1 space-y-4 overflow-y-auto px-4 pb-4"
        style={{ paddingBottom: "calc(11rem + env(safe-area-inset-bottom, 0px))" }}
      >
        {statusLine ? (
          <p className="text-sm font-medium text-sea-700">{statusLine}</p>
        ) : null}
        {liveTranscript ? (
          <p className="rounded-xl bg-ink-50 px-3 py-2 text-sm text-ink-700">
            “{liveTranscript}”
          </p>
        ) : null}

        {run?.status === "needs_clarification" ? (
          <section className="rounded-2xl border border-ember-400/40 bg-white p-4 shadow-panel">
            <p className="text-xs font-semibold uppercase tracking-wider text-ember-600">
              Which one?
            </p>
            <p className="mt-2 font-display text-2xl font-semibold text-ink-950">
              {run.question}
            </p>
            <div className="mt-4 grid gap-3">
              {run.candidates.map((c) => (
                <button
                  key={c.entity_id}
                  type="button"
                  disabled={busy || thinking}
                  onClick={() => onPickCandidate(c)}
                  className="rounded-2xl border border-ink-200 bg-ink-50 px-4 py-5 text-left text-lg font-semibold text-ink-900 disabled:opacity-50"
                >
                  {c.name}
                </button>
              ))}
            </div>
          </section>
        ) : null}

        {run?.status === "awaiting_confirmation" ? (
          <section className="rounded-2xl border border-sea-500/30 bg-white p-4 shadow-panel">
            <p className="text-xs font-semibold uppercase tracking-wider text-sea-600">
              Confirm actions
            </p>
            <p className="mt-2 text-base text-ink-800">
              {run.action_plan?.spoken_summary ?? run.question ?? "Confirm?"}
            </p>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <button
                type="button"
                disabled={busy || thinking}
                onClick={onConfirmActions}
                className="rounded-2xl bg-sea-600 px-4 py-4 text-lg font-semibold text-white disabled:opacity-50"
              >
                Confirm
              </button>
              <button
                type="button"
                disabled={busy || thinking}
                onClick={onCancelActions}
                className="rounded-2xl border border-ink-300 px-4 py-4 text-lg font-semibold text-ink-800 disabled:opacity-50"
              >
                Cancel
              </button>
            </div>
          </section>
        ) : null}

        {brief && run?.status === "done" ? (
          <section className="space-y-3 rounded-2xl border border-ink-200 bg-white p-4 shadow-panel">
            <div className="flex flex-wrap items-start gap-2">
              <p className="font-display text-2xl font-semibold text-ink-950">
                {brief.headline}
              </p>
              {conflictField ||
              brief.points.some((p) => p.kind === "catch_up" && p.evidence_ids.length > 1) ? (
                <span className="rounded-md bg-amber-100 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-amber-900">
                  sources disagree
                </span>
              ) : null}
            </div>
            <ol className="space-y-3">
              {brief.points.map((p, i) => (
                <li key={`${i}-${p.text.slice(0, 24)}`} className="rounded-xl bg-ink-50 p-3">
                  <p className="text-sm text-ink-900">
                    <span className="mr-2 font-semibold text-ink-500">{i + 1}.</span>
                    {p.text}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {p.evidence_ids.map((id) => {
                      const ev = run.evidence?.[id] as Evidence | undefined;
                      if (!ev || !isEvidenceSource(ev.source)) return null;
                      return (
                        <SourceChip
                          key={id}
                          evidence={ev}
                          expanded={expandedChip === id}
                          onToggle={() =>
                            setExpandedChip((cur) => (cur === id ? null : id))
                          }
                        />
                      );
                    })}
                  </div>
                </li>
              ))}
            </ol>
            {run.speech || run.utterance?.text ? (
              <div>
                <button
                  type="button"
                  onClick={() => setShowTranscript((v) => !v)}
                  className="text-xs font-semibold uppercase tracking-wider text-ink-400"
                >
                  {showTranscript ? "Hide transcript" : "Show transcript"}
                </button>
                {showTranscript ? (
                  <p className="mt-2 text-xs leading-relaxed text-ink-500">
                    {run.utterance?.text ? `Heard: ${run.utterance.text}` : null}
                    {run.speech ? (
                      <>
                        {run.utterance?.text ? <br /> : null}
                        Spoken: {run.speech}
                      </>
                    ) : null}
                  </p>
                ) : null}
              </div>
            ) : null}
          </section>
        ) : null}

        {hint ? (
          <p className="break-words text-center text-sm text-ember-600">{hint}</p>
        ) : null}

        <button
          type="button"
          onClick={() => setTraceOpen((v) => !v)}
          className="text-left text-xs font-semibold uppercase tracking-wider text-ink-400"
        >
          {traceOpen ? "Hide trace" : "Show trace"}
        </button>
        {traceOpen ? (
          <div className="max-h-56 overflow-y-auto rounded-xl border border-ink-100 bg-ink-50 p-2">
            <TraceTimeline events={events} />
          </div>
        ) : null}
      </div>

      <div
        className="fixed inset-x-0 bottom-0 z-30 flex flex-col items-center bg-white/95 px-4 pt-3"
        style={{ paddingBottom: "calc(1rem + env(safe-area-inset-bottom, 0px))" }}
      >
        <button
          ref={talkBtnRef}
          type="button"
          disabled={busy || thinking}
          onPointerDown={(e) => {
            e.preventDefault();
            e.currentTarget.setPointerCapture(e.pointerId);
            void startHold();
          }}
          onPointerUp={() => void endHold()}
          onPointerCancel={() => void endHold()}
          onContextMenu={(e) => e.preventDefault()}
          style={{
            userSelect: "none",
            WebkitUserSelect: "none",
            WebkitTouchCallout: "none",
            WebkitTapHighlightColor: "transparent",
            touchAction: "none",
          }}
          className={`relative flex h-28 w-28 select-none touch-none flex-col items-center justify-center rounded-full text-base font-semibold text-white shadow-lg transition active:scale-95 disabled:opacity-50 ${
            recording ? "recording-pulse bg-ember-600" : thinking ? "bg-ink-700" : "bg-sea-600"
          }`}
        >
          {thinking ? (
            <span
              className="h-7 w-7 animate-spin rounded-full border-2 border-white/30 border-t-white"
              aria-hidden
            />
          ) : (
            <span
              className="select-none"
              style={{
                userSelect: "none",
                WebkitUserSelect: "none",
                WebkitTouchCallout: "none",
              }}
            >
              {recording ? formatTimer(elapsedMs) : "Hold"}
            </span>
          )}
        </button>
        <p className="mt-2 text-center text-xs text-ink-400">
          {recording ? "Listening…" : thinking ? "Working…" : "Release to send"}
        </p>
      </div>
    </div>
  );
}
