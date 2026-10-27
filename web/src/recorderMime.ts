/** Prefer iOS AAC mp4, else Opus webm. */
export function pickRecorderMime(): string {
  if (typeof MediaRecorder === "undefined") return "audio/webm";
  if (MediaRecorder.isTypeSupported("audio/mp4")) return "audio/mp4";
  if (MediaRecorder.isTypeSupported("audio/webm;codecs=opus")) {
    return "audio/webm;codecs=opus";
  }
  return "audio/webm";
}

export function recordingFilename(mimeType: string): string {
  return mimeType.includes("mp4") ? "recording.mp4" : "recording.webm";
}

/** Minimum usable MediaRecorder blob (~init + a little audio). */
export const MIN_RECORDING_BYTES = 2048;
export const MIN_HOLD_MS = 500;

/**
 * Tap-to-talk helper: calls onSilence once the speaker pauses (after they've said
 * something), or after maxMs. Returns a cleanup function. Must be started from a
 * user gesture on iOS (AudioContext).
 */
export function watchSilence(
  stream: MediaStream,
  onSilence: () => void,
  { silenceMs = 1400, maxMs = 30000, minSpeechMs = 350 }: { silenceMs?: number; maxMs?: number; minSpeechMs?: number } = {},
): () => void {
  const Ctx: typeof AudioContext | undefined =
    typeof window !== "undefined"
      ? window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      : undefined;
  let done = false;
  const fire = () => {
    if (done) return;
    done = true;
    cleanup();
    onSilence();
  };
  const maxTimer = window.setTimeout(fire, maxMs);
  if (!Ctx) {
    return () => {
      done = true;
      window.clearTimeout(maxTimer);
    };
  }
  const ctx = new Ctx();
  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  const started = performance.now();
  let floor = 0.01;
  let speechMs = 0;
  let quietSince = 0;
  let last = performance.now();
  const tick = window.setInterval(() => {
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (const v of buf) sum += v * v;
    const rms = Math.sqrt(sum / buf.length);
    const now = performance.now();
    const dt = now - last;
    last = now;
    if (now - started < 300) floor = Math.max(floor, rms); // ambient noise estimate
    const loud = rms > Math.max(0.02, floor * 2.2);
    if (loud) {
      speechMs += dt;
      quietSince = 0;
    } else if (speechMs >= minSpeechMs) {
      quietSince = quietSince || now;
      if (now - quietSince >= silenceMs) fire();
    }
  }, 80);
  function cleanup() {
    window.clearInterval(tick);
    window.clearTimeout(maxTimer);
    try {
      source.disconnect();
      void ctx.close();
    } catch {
      /* already closed */
    }
  }
  return () => {
    done = true;
    cleanup();
  };
}
