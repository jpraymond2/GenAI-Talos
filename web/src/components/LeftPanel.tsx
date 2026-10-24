import { useEffect, useRef, useState } from "react";
import { isMockMode, sendVoice } from "../api";
import { unlockSpeech } from "../speech";
import type { TranscriptEntry } from "../types";

interface Props {
  transcript: TranscriptEntry[];
  busy: boolean;
  sampleMode: boolean;
  onSubmit: (text: string) => void;
  /** Called from the hold-to-talk gesture so speechSynthesis is unlocked. */
  onVoiceResult?: (transcript: string) => void;
}

export function LeftPanel({
  transcript,
  busy,
  sampleMode,
  onSubmit,
  onVoiceResult,
}: Props) {
  const [text, setText] = useState("");
  const [recording, setRecording] = useState(false);
  const [voiceHint, setVoiceHint] = useState<string | null>(null);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const holdingRef = useRef(false);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript]);

  async function startHold() {
    if (busy || holdingRef.current) return;
    holdingRef.current = true;
    unlockSpeech();
    setVoiceHint(null);

    if (isMockMode()) {
      setRecording(true);
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "audio/webm";
      const recorder = new MediaRecorder(stream, { mimeType: mime });
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data);
      };
      mediaRef.current = recorder;
      recorder.start(100);
      setRecording(true);
    } catch {
      holdingRef.current = false;
      setVoiceHint("Microphone permission denied.");
    }
  }

  async function endHold() {
    if (!holdingRef.current) return;
    holdingRef.current = false;
    setRecording(false);

    if (isMockMode()) {
      const result = await sendVoice(new Blob());
      const transcriptText = result?.transcript;
      if (transcriptText) {
        if (onVoiceResult) onVoiceResult(transcriptText);
        else onSubmit(transcriptText);
      }
      return;
    }

    const recorder = mediaRef.current;
    mediaRef.current = null;
    if (!recorder) return;

    const blob = await new Promise<Blob>((resolve) => {
      recorder.onstop = () => {
        resolve(new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" }));
      };
      try {
        recorder.stop();
      } catch {
        resolve(new Blob());
      }
    });
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;

    if (!blob.size) {
      setVoiceHint("No audio captured.");
      return;
    }
    const result = await sendVoice(blob);
    if (result?.transcript) {
      if (onVoiceResult) onVoiceResult(result.transcript);
      else onSubmit(result.transcript);
    } else {
      setVoiceHint("Voice endpoint returned no transcript.");
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    unlockSpeech();
    onSubmit(trimmed);
    setText("");
  }

  return (
    <section className="flex h-full min-h-[420px] flex-col rounded-2xl border border-ink-200/80 bg-white/70 p-4 shadow-panel backdrop-blur-sm md:p-5">
      <header className="mb-4 flex items-start justify-between gap-3">
        <div>
          <p className="font-display text-2xl font-semibold tracking-tight text-ink-950">
            Recall
          </p>
          <p className="mt-1 text-sm text-ink-500">Voice-first meeting prep</p>
        </div>
        {sampleMode ? (
          <span className="rounded-md bg-sea-500/15 px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-sea-600">
            Sample mode
          </span>
        ) : null}
      </header>

      <div className="flex flex-1 flex-col items-center justify-center py-4">
        <button
          type="button"
          disabled={busy}
          onPointerDown={(e) => {
            e.preventDefault();
            (e.currentTarget as HTMLButtonElement).setPointerCapture(e.pointerId);
            void startHold();
          }}
          onPointerUp={() => void endHold()}
          onPointerCancel={() => void endHold()}
          onPointerLeave={() => {
            if (holdingRef.current) void endHold();
          }}
          className={`relative flex h-36 w-36 select-none flex-col items-center justify-center rounded-full border-4 border-sea-500/30 bg-gradient-to-b from-sea-500 to-sea-600 text-white shadow-lg transition hover:brightness-110 disabled:opacity-60 ${
            recording ? "recording-pulse" : ""
          }`}
          aria-pressed={recording}
        >
          <MicIcon />
          <span className="mt-2 text-xs font-semibold uppercase tracking-wider">
            {recording ? "Listening" : "Hold to talk"}
          </span>
        </button>
        {voiceHint ? (
          <p className="mt-3 max-w-[16rem] text-center text-xs text-ink-400">{voiceHint}</p>
        ) : (
          <p className="mt-3 text-center text-xs text-ink-400">
            Hold the button to talk — or type below
          </p>
        )}
      </div>

      <div className="scroll-thin mb-3 max-h-48 flex-1 space-y-2 overflow-y-auto rounded-xl bg-ink-50/80 p-3">
        {transcript.length === 0 ? (
          <p className="text-sm text-ink-400">Conversation will appear here.</p>
        ) : (
          transcript.map((entry) => (
            <div
              key={entry.id}
              className={`animate-rise rounded-lg px-3 py-2 text-sm ${
                entry.role === "user"
                  ? "ml-6 bg-sea-500 text-white"
                  : entry.role === "system"
                    ? "bg-transparent text-ink-400"
                    : "mr-6 bg-white text-ink-800 shadow-sm"
              }`}
            >
              {entry.text}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={submit} className="flex gap-2">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Type an utterance…"
          disabled={busy}
          className="min-w-0 flex-1 rounded-xl border border-ink-200 bg-white px-3 py-2.5 text-sm outline-none ring-sea-500/40 focus:ring-2"
        />
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="rounded-xl bg-ink-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-ink-800 disabled:opacity-40"
        >
          Ask
        </button>
      </form>
    </section>
  );
}

function MicIcon() {
  return (
    <svg className="h-10 w-10" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v5a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.9V21h2v-3.1A7 7 0 0 0 19 11h-2Z" />
    </svg>
  );
}
