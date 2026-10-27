/**
 * Recall's voice. Answers are spoken with NVIDIA Magpie TTS (POST /api/tts); if that's
 * unavailable or slow, the browser's built-in voice takes over so Recall never goes silent.
 * Call unlockSpeech() from a tap/click first — iOS only lets audio play after a gesture.
 */

import { shouldMock } from "./api";
import { authHeaders } from "./auth";

const MAGPIE_TIMEOUT_MS = 6000;
// 0.1 s of silence: playing it inside the tap unlocks this <audio> element for later answers.
const SILENT_WAV =
  "data:audio/wav;base64,UklGRigAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQQAAAAAAAAA";

let player: HTMLAudioElement | null = null;
let currentUrl: string | null = null;
let generation = 0;
let magpieDown = false;

function audio(): HTMLAudioElement | null {
  if (typeof window === "undefined") return null;
  if (!player) {
    player = new Audio();
    player.preload = "auto";
  }
  return player;
}

export function unlockSpeech(): void {
  if (typeof window === "undefined") return;
  const a = audio();
  if (a && a.paused) {
    a.src = SILENT_WAV;
    void a.play().catch(() => undefined);
  }
  if (window.speechSynthesis) {
    window.speechSynthesis.cancel();
    const warm = new SpeechSynthesisUtterance("");
    warm.volume = 0;
    window.speechSynthesis.speak(warm);
  }
}

export function stopSpeaking(): void {
  generation += 1;
  player?.pause();
  if (typeof window !== "undefined") window.speechSynthesis?.cancel();
}

function browserSay(parts: string[]): void {
  if (typeof window === "undefined" || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const voices = window.speechSynthesis.getVoices();
  // Prefer the nicer built-in voices when the browser has them.
  const nice = voices.find((v) => /natural|neural|google us english|samantha|aria|jenny/i.test(v.name) && v.lang.startsWith("en"));
  for (const part of parts) {
    const u = new SpeechSynthesisUtterance(part);
    if (nice) u.voice = nice;
    u.rate = 1.03;
    window.speechSynthesis.speak(u);
  }
}

async function magpieSay(text: string, ticket: number): Promise<boolean> {
  if (magpieDown || (await shouldMock())) return false;
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), MAGPIE_TIMEOUT_MS);
  try {
    const res = await fetch("/api/tts", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ text }),
      signal: ctrl.signal,
    });
    if (res.status === 503) magpieDown = true; // not configured on this server: stop trying this session
    if (!res.ok) return false;
    const blob = await res.blob();
    if (ticket !== generation) return true; // something newer is already speaking
    const a = audio();
    if (!a) return false;
    if (currentUrl) URL.revokeObjectURL(currentUrl);
    currentUrl = URL.createObjectURL(blob);
    a.src = currentUrl;
    await a.play();
    return true;
  } catch {
    return false;
  } finally {
    window.clearTimeout(timer);
  }
}

/**
 * Speak an answer. `quick` lines ("One sec", "Pulling up Omar…") use the browser voice
 * because a round trip would make them arrive late.
 */
export function speakText(text: string, opts?: { quick?: boolean }): void {
  const clean = text.trim();
  if (!clean) return;
  if (opts?.quick) {
    browserSay([clean]);
    return;
  }
  generation += 1;
  const ticket = generation;
  window.speechSynthesis?.cancel();
  void magpieSay(clean, ticket).then((ok) => {
    if (!ok && ticket === generation) browserSay([clean]);
  });
}

export function speakBrief(headline: string, points: string[] = []): void {
  const parts = [headline, ...points].map((p) => p.trim().replace(/[.!?]+$/, "")).filter(Boolean);
  if (parts.length) speakText(parts.join(". ") + ".");
}
