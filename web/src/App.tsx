import { useCallback, useEffect, useState } from "react";
import { ask, confirm, isMockMode, onTrace } from "./api";
import { CenterPanel } from "./components/CenterPanel";
import { LeftPanel } from "./components/LeftPanel";
import { RightPanel } from "./components/RightPanel";
import type { Candidate, RunState, TraceEvent, TranscriptEntry } from "./types";

export default function App() {
  const [run, setRun] = useState<RunState | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const sampleMode = isMockMode();

  useEffect(() => {
    return onTrace((event) => {
      setEvents((prev) => [...prev, event]);
    });
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

  async function handleAsk(text: string) {
    setBusy(true);
    setEvents([]);
    pushTranscript("user", text);
    try {
      const state = await ask(text);
      setRun(state);
      if (state.status === "needs_clarification" && state.question) {
        pushTranscript("assistant", state.question);
      } else if (state.final_brief) {
        pushTranscript("assistant", state.final_brief.headline);
      }
    } catch (err) {
      pushTranscript("system", err instanceof Error ? err.message : "Ask failed");
    } finally {
      setBusy(false);
    }
  }

  async function handlePick(candidate: Candidate) {
    setBusy(true);
    setEvents([]);
    pushTranscript("user", candidate.name);
    try {
      const state = await confirm({
        entity_id: candidate.entity_id,
        run_id: run?.run_id,
      });
      setRun(state);
      if (state.final_brief) {
        pushTranscript("assistant", state.final_brief.headline);
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirmActions() {
    setBusy(true);
    try {
      const state = await confirm({
        action: "confirm",
        run_id: run?.run_id,
      });
      setRun(state);
      pushTranscript("system", "Actions confirmed (dry-run in sample mode).");
    } finally {
      setBusy(false);
    }
  }

  async function handleCancelActions() {
    setBusy(true);
    try {
      const state = await confirm({
        action: "cancel",
        run_id: run?.run_id,
      });
      setRun(state);
      pushTranscript("system", "Actions cancelled.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-grain px-3 py-4 md:px-6 md:py-6">
      <div className="mx-auto grid max-w-[1400px] gap-4 lg:grid-cols-12 lg:gap-5">
        <div className="lg:col-span-3">
          <LeftPanel
            transcript={transcript}
            busy={busy}
            sampleMode={sampleMode}
            onSubmit={(t) => void handleAsk(t)}
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
