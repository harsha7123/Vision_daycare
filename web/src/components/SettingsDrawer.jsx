import { useState } from "react";
import { api, defaultApiUrl, getApiUrl, setApiUrl } from "../lib/api";
import { PRIORITY, PRIORITY_ORDER, RULES } from "../lib/constants";
import { Icon } from "./Controls";

export const DEFAULT_SETTINGS = {
  centre: "Sunshine Day Care",
  centrePhone: "",
  contacts: [{ id: "demo", name: "Priya Sharma", relation: "Parent", phone: "+91 90000 00000" }],
  primary: "demo",
  autoCall: { critical: true, high: false, medium: false, low: false },
  mode: "simulated",
  template:
    "Hello {parent}. This is an automated safety call from {centre}. At {time}, our camera system flagged: {title}, in {room}, for {duration}. The staff have been alerted and are checking now. Please call the centre if you have any questions.",
};

export default function SettingsDrawer({ open, onClose, settings, setSettings, callLog, clearLog, serverCfg, onServerChange }) {
  const [draft, setDraft] = useState({ name: "", relation: "Parent", phone: "" });
  const [url, setUrl] = useState(getApiUrl());
  const [test, setTest] = useState("");
  if (!open) return null;
  const upd = (patch) => setSettings((s) => ({ ...s, ...patch }));
  const twilio = serverCfg?.notify?.twilio;

  const addContact = () => {
    if (!draft.name.trim() || !draft.phone.trim()) return;
    const c = { ...draft, id: crypto.randomUUID?.() || String(Date.now()) };
    upd({ contacts: [...settings.contacts, c], primary: settings.primary || c.id });
    setDraft({ name: "", relation: "Parent", phone: "" });
  };

  const testServer = async () => {
    setApiUrl(url.trim() === defaultApiUrl ? "" : url.trim());
    setTest("Checking...");
    try {
      await api.health();
      setTest("Connected");
      onServerChange();
    } catch (e) { setTest(e.message); }
  };

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()} aria-label="Contacts and settings">
        <div className="drawer-head">
          <h2>Contacts & calling</h2>
          <button className="btn icon-btn ghost" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>

        <section>
          <h3>Centre</h3>
          <div className="grid2">
            <label className="field"><span className="label">Centre name</span>
              <input className="input" value={settings.centre} onChange={(e) => upd({ centre: e.target.value })} /></label>
            <label className="field"><span className="label">Centre phone (read out in calls)</span>
              <input className="input" value={settings.centrePhone} onChange={(e) => upd({ centrePhone: e.target.value })} placeholder="+91 ..." /></label>
          </div>
        </section>

        <section>
          <h3>Parents to call</h3>
          <ul className="contact-list">
            {settings.contacts.map((c) => (
              <li key={c.id}>
                <label className="radio">
                  <input type="radio" name="primary" checked={settings.primary === c.id} onChange={() => upd({ primary: c.id })} />
                  <span><b>{c.name}</b> <span className="muted small">{c.relation} · {c.phone}</span></span>
                </label>
                <button className="btn small ghost" onClick={() => upd({ contacts: settings.contacts.filter((x) => x.id !== c.id), primary: settings.primary === c.id ? settings.contacts.find((x) => x.id !== c.id)?.id : settings.primary })}
                  aria-label={`Remove ${c.name}`}><Icon name="x" size={14} /></button>
              </li>
            ))}
          </ul>
          <div className="add-contact">
            <input className="input" placeholder="Name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            <select className="select" value={draft.relation} onChange={(e) => setDraft({ ...draft, relation: e.target.value })}>
              {["Parent", "Mother", "Father", "Guardian", "Centre admin", "Centre owner"].map((r) => <option key={r}>{r}</option>)}
            </select>
            <input className="input" placeholder="+91 98765 43210" value={draft.phone} onChange={(e) => setDraft({ ...draft, phone: e.target.value })} />
            <button className="btn primary" onClick={addContact}>Add</button>
          </div>
          <p className="muted small">The selected contact is called. Contacts are stored only in this browser.</p>
        </section>

        <section>
          <h3>When to call automatically</h3>
          <p className="muted small">While a video plays, reaching an alert with these priorities starts a call.</p>
          <div className="chips">
            {PRIORITY_ORDER.map((p) => (
              <button key={p} className={`chip ${settings.autoCall[p] ? "on" : ""}`} aria-pressed={!!settings.autoCall[p]}
                onClick={() => upd({ autoCall: { ...settings.autoCall, [p]: !settings.autoCall[p] } })}>
                <span className="dot" style={{ background: PRIORITY[p].color }} />{PRIORITY[p].label}
              </button>
            ))}
          </div>
          <div className="field">
            <span className="label">Call type</span>
            <div className="segmented" role="group">
              <button className={settings.mode === "simulated" ? "on" : ""} onClick={() => upd({ mode: "simulated" })}>Simulated (demo)</button>
              <button className={settings.mode === "real" ? "on" : ""} disabled={!twilio} onClick={() => upd({ mode: "real" })}
                title={twilio ? "" : "Set TWILIO_* variables on the server to enable"}>Real phone call</button>
            </div>
            <p className="muted small">
              {twilio
                ? "Real calls go through Twilio, and only to numbers in the server's ALLOWED_CALL_NUMBERS list."
                : "Real calls are off. The server has no Twilio account set (see the README). Simulated calls ring here and read the message aloud."}
            </p>
          </div>
          <label className="field"><span className="label">Call message. Placeholders: {"{parent} {centre} {time} {title} {room} {duration} {phone}"}</span>
            <textarea className="input" rows={4} value={settings.template} onChange={(e) => upd({ template: e.target.value })} /></label>
          <button className="link small" onClick={() => upd({ template: DEFAULT_SETTINGS.template })}>Reset message</button>
        </section>

        <section>
          <h3>Call log</h3>
          {callLog.length ? (
            <>
              <ul className="log">
                {callLog.slice().reverse().map((c, i) => (
                  <li key={i}>
                    <span className="dot" style={{ background: PRIORITY[c.priority]?.color }} />
                    <span>{new Date(c.at).toLocaleTimeString()} · <b>{c.contact}</b> · {RULES[c.rule]?.title} · <span className="muted">{c.outcome}</span></span>
                  </li>
                ))}
              </ul>
              <button className="link small" onClick={clearLog}>Clear log</button>
            </>
          ) : <p className="muted small">No calls yet.</p>}
        </section>

        <section>
          <h3>Analysis server</h3>
          <div className="add-contact">
            <input className="input grow" value={url} onChange={(e) => setUrl(e.target.value)} aria-label="Server URL" />
            <button className="btn" onClick={testServer}>Save & test</button>
          </div>
          {test && <p className={`small ${test === "Connected" ? "ok-text" : "err-text"}`}>{test}</p>}
          <p className="muted small">Default: {defaultApiUrl}</p>
        </section>
      </aside>
    </div>
  );
}
