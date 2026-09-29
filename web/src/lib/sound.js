let ctx;
const audio = () => {
  try {
    ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state === "suspended") ctx.resume();
    return ctx;
  } catch {
    return null;
  }
};

function tone(freq, start, dur, gain = 0.12) {
  const c = audio();
  if (!c) return;
  const o = c.createOscillator();
  const g = c.createGain();
  o.frequency.value = freq;
  o.connect(g);
  g.connect(c.destination);
  const t = c.currentTime + start;
  g.gain.setValueAtTime(gain, t);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  o.start(t);
  o.stop(t + dur + 0.02);
}

export function alertBeep(priority) {
  const seq = priority === "critical" ? [880, 660, 880, 660] : priority === "high" ? [760, 760] : [620];
  seq.forEach((f, i) => tone(f, i * 0.2, 0.17));
}

/** Classic two-tone phone ring; returns a stop() function. */
export function startRinging() {
  let stopped = false;
  const ring = () => {
    if (stopped) return;
    for (let i = 0; i < 10; i++) {
      tone(440, i * 0.1, 0.09, 0.05);
      tone(480, i * 0.1, 0.09, 0.05);
    }
  };
  ring();
  const id = setInterval(ring, 3000);
  return () => { stopped = true; clearInterval(id); };
}

export function speak(text, onEnd) {
  if (!("speechSynthesis" in window)) { onEnd?.(); return () => {}; }
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 0.95;
  const voices = window.speechSynthesis.getVoices();
  u.voice = voices.find((v) => /en-IN/i.test(v.lang)) || voices.find((v) => /^en/i.test(v.lang)) || null;
  u.onend = () => onEnd?.();
  window.speechSynthesis.speak(u);
  return () => window.speechSynthesis.cancel();
}
