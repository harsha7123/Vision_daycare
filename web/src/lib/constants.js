// Plain, meaningful colours: red = danger, orange = warning, amber = caution, blue = adult, green = child.
export const PRIORITY = {
  critical: { label: "Critical", color: "#C62828", bg: "#FDECEA", border: "#F5C2BE" },
  high: { label: "High", color: "#D84315", bg: "#FBE9E7", border: "#F7C6B8" },
  medium: { label: "Medium", color: "#A86B00", bg: "#FFF6DC", border: "#F2DDA0" },
  low: { label: "Low", color: "#546E7A", bg: "#ECEFF1", border: "#CFD8DC" },
};
export const PRIORITY_ORDER = ["critical", "high", "medium", "low"];

export const ROLE = {
  adult: { label: "Adult", color: "#1565C0", bg: "#E3F0FC" },
  child: { label: "Child", color: "#2E7D32", bg: "#E6F4E7" },
};
export const ALERT_COLOR = "#D32F2F";
export const PHONE_COLOR = "#F9A825";

export const ZONES = {
  play: { label: "Play area", hint: "Children's area. A caretaker must be inside it.", color: "#2E7D32" },
  nap: { label: "Nap area", hint: "Lying down is normal here", color: "#00838F" },
  exit: { label: "Exit / door", hint: "Doorways", color: "#EF6C00" },
  staff_only: { label: "Staff only", hint: "Kitchen, office", color: "#6D4C41" },
};

export const RULES = {
  R1_phone_use: { code: "R1", title: "Caretaker on phone", desc: "An adult keeps a phone in hand, looking at it or on a call." },
  R2_left_zone: { code: "R2", title: "Caretaker left the play area", desc: "Children are in the play area, but a caretaker stays outside it." },
  R3_unattended: { code: "R3", title: "Children left unattended", desc: "At least one child is in the play area with no adult in it." },
  R4_ratio: { code: "R4", title: "Too many children per adult", desc: "The children-to-adult ratio is above the centre's policy." },
  R5_fall: { code: "R5", title: "Child fell / lying on floor", desc: "A child is lying down outside the nap area." },
  R6_idle: { code: "R6", title: "Caretaker idle or asleep", desc: "An adult sits head-down and motionless while children are present." },
};

export const PRESETS = {
  quick: { label: "Short clips", desc: "Seconds-long thresholds for internet clips under 1 minute" },
  demo: { label: "Demo", desc: "Roughly 10-20 s thresholds, good for 1-3 minute videos" },
  production: { label: "Real centre", desc: "Blueprint values: 60 s unattended, 2 min on phone, 3 min away" },
};

export const fmtTime = (s) => {
  if (!Number.isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${m}:${String(r).padStart(2, "0")}`;
};
export const fmtDur = (s) => (s >= 60 ? `${Math.floor(s / 60)} min ${Math.round(s % 60)} s` : `${Math.round(s)} s`);
