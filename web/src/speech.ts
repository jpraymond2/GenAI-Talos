/** Browser TTS helpers — call unlockSpeech() from a user-gesture handler first. */

export function unlockSpeech(): void {
  if (typeof window === "undefined" || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const warm = new SpeechSynthesisUtterance("");
  warm.volume = 0;
  window.speechSynthesis.speak(warm);
}

export function speakBrief(headline: string, points: string[] = []): void {
  if (typeof window === "undefined" || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const parts = [headline, ...points].map((p) => p.trim()).filter(Boolean);
  for (const part of parts) {
    const u = new SpeechSynthesisUtterance(part);
    u.rate = 1.05;
    window.speechSynthesis.speak(u);
  }
}

export function speakText(text: string): void {
  if (!text.trim()) return;
  speakBrief(text.trim(), []);
}
