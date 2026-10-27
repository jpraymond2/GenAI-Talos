import { useCallback, useEffect, useState } from "react";
import { ask, confirm, isMockMode, onTrace } from "./api";
import { getApiToken, getUser, logout } from "./auth";
import { CenterPanel } from "./components/CenterPanel";
import { ContactsPage, IdentifiedContact } from "./components/Contacts";
import { LeftPanel } from "./components/LeftPanel";
import { LoginPage } from "./components/LoginPage";
import { PhoneView } from "./components/PhoneView";
import { RightPanel } from "./components/RightPanel";
import { SyncPage } from "./components/SyncPage";
import { TopBar, type Tab } from "./components/TopBar";
import { speakBrief, speakText, unlockSpeech } from "./speech";
import type { Candidate, DeviceMode, RunState, SessionUser, TraceEvent, TranscriptEntry } from "./types";

const DEVICE_KEY = "recall_device";

function initialDevice(): DeviceMode {
  try {
    const saved = localStorage.getItem(DEVICE_KEY);
    if (saved === "mac" || saved === "phone") return saved;
  } catch {
    /* ignore */
  }
  return typeof window !== "undefined" && window.matchMedia("(max-width: 640px)").matches ? "phone" : "mac";
}

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
  const sampleMode = isMockMode();
  const isPhone = useIsPhone();
  const [user, setUserState] = useState<SessionUser | null>(() => (getApiToken() ? getUser() ?? { name: "Recall user", email: "" } : null));
  const [device, setDevice] = useState<DeviceMode>(initialDevice);
  const [tab, setTab] = useState<Tab>("assistant");
  const [focusContact, setFocusContact] = useState<string | null>(null);

  function chooseDevice(d: DeviceMode) {
    setDevice(d);
    try {
      localStorage.setItem(DEVICE_KEY, d);
    } catch {
      /* ignore */
    }
  }

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

  if (!user) {
    return <LoginPage onLoggedIn={setUserState} />;
  }

  const identified =
    run?.entity_id && (run.status === "done" || run.status === "awaiting_confirmation") ? run.entity_id : null;

  const phoneView = (
    <PhoneView
      run={run}
      events={events}
      busy={busy}
      showHeader={false}
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
      contactSlot={identified ? <IdentifiedContact entityId={identified} compact /> : null}
    />
  );

  let body: React.ReactNode;
  if (tab === "contacts") {
    body = <ContactsPage onGoSync={() => setTab("sync")} focusId={focusContact} />;
  } else if (tab === "sync") {
    body = <SyncPage device={device} onSwitchDevice={chooseDevice} onViewContacts={() => setTab("contacts")} />;
  } else if (isPhone) {
    body = phoneView;
  } else if (device === "phone") {
    // Desktop preview of the phone app, inside a device frame.
    body = (
      <div className="flex justify-center py-2">
        <div className="relative h-[780px] w-[392px] overflow-hidden rounded-[46px] border-[10px] border-ink-900 bg-white shadow-2xl" style={{ transform: "translateZ(0)" }}>
          <div className="absolute left-1/2 top-2 z-40 h-6 w-28 -translate-x-1/2 rounded-full bg-ink-900" aria-hidden />
          <div className="scroll-thin h-full overflow-y-auto pt-8">{phoneView}</div>
        </div>
      </div>
    );
  } else {
    body = (
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
        <div className="space-y-4 lg:col-span-4">
          {identified ? <IdentifiedContact entityId={identified} /> : null}
          {identified ? (
            <button
              type="button"
              onClick={() => {
                setFocusContact(identified);
                setTab("contacts");
              }}
              className="-mt-2 w-full text-right text-xs font-semibold text-sea-600 hover:underline"
            >
              Open in Contacts →
            </button>
          ) : null}
          <RightPanel events={events} entityId={run?.entity_id ?? null} />
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-[100dvh] bg-grain">
      <TopBar
        device={device}
        onDevice={chooseDevice}
        tab={tab}
        onTab={(t) => {
          setFocusContact(null);
          setTab(t);
        }}
        user={user}
        onLogout={() => {
          logout();
          setUserState(null);
        }}
        sampleMode={sampleMode}
      />
      <main className={tab === "assistant" && isPhone ? "" : "px-3 py-4 md:px-6 md:py-6"}>{body}</main>
    </div>
  );
}
