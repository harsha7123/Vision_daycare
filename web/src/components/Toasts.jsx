import { PRIORITY } from "../lib/constants";

export default function Toasts({ toasts, dismiss }) {
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className="toast" style={{ borderLeftColor: t.priority ? PRIORITY[t.priority].color : "#1565C0" }}>
          <div className="toast-body">
            {t.title && <b>{t.title}</b>}
            {t.text && <div className="muted small">{t.text}</div>}
          </div>
          {t.action && <button className="btn small" onClick={() => { t.action.fn(); dismiss(t.id); }}>{t.action.label}</button>}
          <button className="x" onClick={() => dismiss(t.id)} aria-label="Dismiss">×</button>
        </div>
      ))}
    </div>
  );
}
