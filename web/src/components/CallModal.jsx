import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { PRIORITY, RULES, fmtTime } from "../lib/constants";
import { speak, startRinging } from "../lib/sound";
import { Icon } from "./Controls";

export function buildMessage(template, { contact, event, settings }) {
  const when = new Date(event.created_at || Date.now());
  const vars = {
    parent: contact?.name?.split(" ")[0] || "there",
    centre: settings.centre || "the day-care centre",
    room: event.room || "the room",
    title: (RULES[event.rule]?.title || event.title).toLowerCase(),
    time: `${when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`,
    duration: `${Math.round(event.duration_s)} seconds`,
    phone: settings.centrePhone || "",
  };
  return template.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");
}

const initials = (name) => (name || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

/**
 * Phone-call screen. mode "simulated": ring, "answer", read the alert aloud with speech synthesis.
 * mode "real": ask the backend to place a Twilio call to the contact's phone.
 */
export default function CallModal({ call, settings, onClose, onLog }) {
  const { contact, event, mode } = call;
  const message = buildMessage(settings.template, { contact, event, settings });
  const [state, setState] = useState(mode === "real" ? "placing" : "ringing");
  const [secs, setSecs] = useState(0);
  const [info, setInfo] = useState("");
  const [sms, setSms] = useState("");
  const stopRing = useRef(() => {});
  const stopSpeech = useRef(() => {});
  const logged = useRef(false);

  const log = (outcome) => {
    if (logged.current) return;
    logged.current = true;
    onLog({ at: Date.now(), contact: contact.name, phone: contact.phone, rule: event.rule, priority: event.priority, mode, outcome, t: event.t });
  };

  useEffect(() => {
    let timers = [];
    if (mode === "real") {
      api.call(contact.phone.replace(/[\s-]/g, ""), message)
        .then((r) => { setState("placed"); setInfo(`Twilio call ${r.status || "queued"} (${r.sid?.slice(0, 10)}...)`); log("placed via Twilio"); })
        .catch((e) => { setState("failed"); setInfo(e.message); log(`failed: ${e.message}`); });
    } else {
      stopRing.current = startRinging();
      timers.push(setTimeout(() => {
        stopRing.current();
        setState("connected");
        log("answered (simulated)");
        stopSpeech.current = speak(message, () => timers.push(setTimeout(() => setState("ended"), 1200)));
      }, 3500));
    }
    return () => { timers.forEach(clearTimeout); stopRing.current(); stopSpeech.current(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (state !== "connected") return;
    const id = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [state]);

  const hangUp = () => {
    stopRing.current();
    stopSpeech.current();
    if (state === "ringing") log("cancelled");
    setState("ended");
    setTimeout(onClose, 500);
  };

  const sendSms = async () => {
    if (mode !== "real") { setSms("SMS sent (simulated)"); return; }
    try {
      await api.sms(contact.phone.replace(/[\s-]/g, ""), message);
      setSms("SMS sent");
    } catch (e) { setSms(e.message); }
  };

  const p = PRIORITY[event.priority];
  const status = {
    ringing: "Calling...", placing: "Placing call...", placed: "Ringing on their phone",
    connected: `Connected · ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`, ended: "Call ended", failed: "Call failed",
  }[state];

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label={`Calling ${contact.name}`}>
      <div className="call-card">
        <div className="call-reason" style={{ background: p.bg, color: p.color, borderColor: p.border }}>
          <Icon name="alert" size={16} /> {p.label}: {RULES[event.rule]?.title} at {fmtTime(event.t)}
        </div>
        <div className={`call-avatar ${state === "ringing" || state === "placed" ? "ringing" : ""} ${state === "connected" ? "live" : ""}`}>
          {initials(contact.name)}
        </div>
        <div className="call-name">{contact.name}</div>
        <div className="muted">{contact.relation ? `${contact.relation} · ` : ""}{contact.phone}</div>
        <div className={`call-status ${state}`}>{status}</div>
        <div className="call-mode">{mode === "real" ? "Real phone call via Twilio" : "Simulated call: the message plays through this computer's speaker"}</div>
        {info && <div className={`call-info ${state === "failed" ? "err" : ""}`}>{info}</div>}
        <div className="call-transcript">
          <div className="label">Message {mode === "real" ? "the parent hears" : "being read"}</div>
          <p>{message}</p>
        </div>
        <div className="call-actions">
          <button className="btn" onClick={sendSms}><Icon name="sms" size={16} /> Send as SMS</button>
          <button className="btn hangup" onClick={hangUp}><Icon name="phone" size={16} /> {state === "ended" || state === "failed" || state === "placed" ? "Close" : "End call"}</button>
        </div>
        {sms && <div className="muted small center">{sms}</div>}
      </div>
    </div>
  );
}
