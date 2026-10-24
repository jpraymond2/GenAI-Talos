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
