import { ZONES } from "../lib/constants";
import { Icon } from "./Controls";

/** Controls for drawing zones on the video; state lives in App (editor = {active, zones, draft}). */
export default function ZoneToolbar({ editor, setEditor }) {
  const start = (type) => {
    const n = editor.zones.filter((z) => z.type === type).length + 1;
    setEditor((e) => ({ ...e, draft: { type, name: `${ZONES[type].label}${n > 1 ? ` ${n}` : ""}`, polygon: [] } }));
  };
  const finish = () => setEditor((e) => (e.draft && e.draft.polygon.length >= 3
    ? { ...e, zones: [...e.zones, e.draft], draft: null } : e));
  const undo = () => setEditor((e) => (e.draft ? { ...e, draft: { ...e.draft, polygon: e.draft.polygon.slice(0, -1) } } : e));

  return (
    <div className="zone-toolbar">
      <div className="zone-types">
        {Object.entries(ZONES).map(([k, z]) => (
          <button key={k} className={`zone-btn ${editor.draft?.type === k ? "on" : ""}`} onClick={() => start(k)} title={z.hint}>
            <span className="swatch" style={{ background: z.color }} />
            <span><b>{z.label}</b><span className="muted small block">{z.hint}</span></span>
          </button>
        ))}
      </div>
      <div className="zone-actions">
        <button className="btn small" onClick={undo} disabled={!editor.draft?.polygon.length}>Undo point</button>
        <button className="btn small primary" onClick={finish} disabled={(editor.draft?.polygon.length || 0) < 3}>Finish shape</button>
        <button className="btn small ghost" onClick={() => setEditor((e) => ({ ...e, zones: [], draft: null }))} disabled={!editor.zones.length}>Clear all</button>
      </div>
      {editor.zones.length ? (
        <ul className="zone-chips">
          {editor.zones.map((z, i) => (
            <li key={i} style={{ borderColor: ZONES[z.type].color }}>
              <span className="swatch" style={{ background: ZONES[z.type].color }} />{z.name}
              <button className="x" aria-label={`Delete ${z.name}`} onClick={() => setEditor((e) => ({ ...e, zones: e.zones.filter((_, j) => j !== i) }))}>
                <Icon name="x" size={12} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">No zones yet, so the whole picture counts as the play area. Drawing zones is optional, but it makes "left the play area" and "nap area" work.</p>
      )}
    </div>
  );
}
