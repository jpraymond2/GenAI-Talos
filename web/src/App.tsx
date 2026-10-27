import { useCallback, useEffect, useState } from "react";
import {
  ask,
  confirm,
  enableRecordedDemoMock,
  isBackendUnreachable,
  isMockMode,
  onTrace,
} from "./api";
import { CenterPanel } from "./components/CenterPanel";
import { LeftPanel } from "./components/LeftPanel";
import { PhoneView } from "./components/PhoneView";
import { RightPanel } from "./components/RightPanel";
import { speakBrief, speakText, unlockSpeech } from "./speech";
import type { Candidate, RunState, TraceEvent, TranscriptEntry } from "./types";

function useIsPhone(): boolean {
  const [phone, setPhone] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(max-width: 640px)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 640px)");
    const onChange = () => setPhone(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return phone;
}

export default function App() {
  const [run, setRun] = useState<RunState | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [showMockBanner, setShowMockBanner] = useState(false);
  const sampleMode = isMockMode();
  const isPhone = useIsPhone();

  useEffect(() => {
    return onTrace((event) => {
      setEvents((prev) => [...prev, event]);
    });
  }, []);

  useEffect(() => {
    const id = window.setInterval(() => {
      setShowMockBanner(isBackendUnreachable() && !isMockMode());
    }, 1500);
    return () => window.clearInterval(id);
  }, []);

  const pushTranscript = useCallback((role: TranscriptEntry["role"], text: string) => {
    setTranscript((prev) => [
      ...prev,
      {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        role,
        text,
        ts: new Date().toISOString(),
      },
    ]);
  }, []);

  function speakFromState(state: RunState) {
    if (state.speech) {
      speakText(state.speech);
      return;
    }
    if (state.status === "needs_clarification" && state.question) {
      speakText(state.question);
      return;
    }
    const brief = state.final_brief;
    if (brief) {
      speakBrief(
        brief.headline,
        brief.points.map((p) => p.text),
      );
    }
  }

  async function handleAsk(text: string, opts?: { fromVoice?: boolean; runId?: string }) {
    setBusy(true);
    setEvents([]);
    pushTranscript("user", text);
    try {
      const state = await ask(text, { runId: opts?.runId });
      setRun(state);
      if (state.status === "needs_clarification" && state.question) {
        pushTranscript("assistant", state.question);
      } else if (state.final_brief) {
        pushTranscript("assistant", state.final_brief.headline);
      }
      if (opts?.fromVoice) speakFromState(state);
    } catch (err) {
      pushTranscript("system", err instanceof Error ? err.message : "Ask failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleVoice(transcriptText: string) {
    await handleAsk(transcriptText, {
      fromVoice: true,
      runId:
        run?.status === "needs_clarification" || run?.status === "awaiting_confirmation"
          ? run.run_id
          : undefined,
    });
  }

  async function handlePick(candidate: Candidate) {
    unlockSpeech();
    setBusy(true);
    setEvents([]);
    pushTranscript("user", candidate.name);
    try {
      const state =
        run?.status === "needs_clarification" && run.run_id
          ? await ask(candidate.name, { runId: run.run_id })
          : await ask(run?.utterance?.text ?? `I'm meeting ${candidate.name}`, {
              entityId: candidate.entity_id,
            });
      setRun(state);
      if (state.final_brief) {
        pushTranscript("assistant", state.final_brief.headline);
        speakFromState(state);
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirmActions() {
    if (!run?.run_id) return;
    setBusy(true);
    try {
      const viaAsk = await ask("confirm", { runId: run.run_id });
      if (viaAsk.status === "done") {
        setRun(viaAsk);
        pushTranscript("system", "Actions confirmed.");
        return;
      }
      const result = await confirm({ run_id: run.run_id, confirmed: true });
      pushTranscript(
        "system",
        result.status === "not_implemented"
          ? "Actions not implemented yet."
          : "Actions confirmed (dry-run in sample mode).",
      );
    } finally {
      setBusy(false);
    }
  }

  async function handleCancelActions() {
    if (!run?.run_id) return;
    setBusy(true);
    try {
      const viaAsk = await ask("cancel", { runId: run.run_id });
      setRun(viaAsk);
      pushTranscript("system", "Actions cancelled.");
    } finally {
      setBusy(false);
    }
  }

  if (isPhone) {
    return (
      <div className="min-h-[100dvh] bg-white">
        <PhoneView
          run={run}
          events={events}
          busy={busy}
          onAsk={(t, o) => handleAsk(t, { runId: o?.runId, fromVoice: true })}
          onVoiceState={(state) => {
            setRun(state);
            if (state.status === "needs_clarification" && state.question) {
              pushTranscript("assistant", state.question);
            } else if (state.final_brief) {
              pushTranscript("assistant", state.final_brief.headline);
            }
          }}
          onPickCandidate={(c) => void handlePick(c)}
          onConfirmActions={() => void handleConfirmActions()}
          onCancelActions={() => void handleCancelActions()}
        />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-grain px-3 py-4 md:px-6 md:py-6">
      {sampleMode ? (
        <div className="mx-auto mb-3 max-w-[1400px] rounded border border-amber-700/40 bg-amber-50 px-3 py-2 text-sm font-medium text-amber-900">
          MOCK DATA
        </div>
      ) : null}
      {showMockBanner ? (
        <div className="mx-auto mb-3 flex max-w-[1400px] flex-wrap items-center justify-between gap-3 rounded border border-red-700/50 bg-red-50 px-3 py-2 text-sm text-red-900">
          <span>Backend not reachable on :8000; run `make demo`</span>
          <button
            type="button"
            className="rounded bg-red-800 px-3 py-1 text-white"
            onClick={() => {
              enableRecordedDemoMock();
              setShowMockBanner(false);
            }}
          >
            View recorded demo (mock)
          </button>
        </div>
      ) : null}
      <div className="mx-auto grid max-w-[1400px] gap-4 lg:grid-cols-12 lg:gap-5">
        <div className="lg:col-span-3">
          <LeftPanel
            transcript={transcript}
            busy={busy}
            sampleMode={sampleMode}
            onSubmit={(t) => void handleAsk(t)}
            onVoiceResult={(t) => void handleVoice(t)}
          />
        </div>
        <div className="lg:col-span-5">
          <CenterPanel
            run={run}
            busy={busy}
            onPickCandidate={(c) => void handlePick(c)}
            onConfirmActions={() => void handleConfirmActions()}
            onCancelActions={() => void handleCancelActions()}
          />
        </div>
        <div className="lg:col-span-4">
          <RightPanel events={events} entityId={run?.entity_id ?? null} />
        </div>
      </div>
    </div>
  );
}
