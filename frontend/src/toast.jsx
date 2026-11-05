/**
 * Toast notifications: transient messages about something that just
 * happened.
 *
 * WHAT GOES IN A TOAST, AND WHAT DOES NOT
 *   A toast reports an EVENT - a CV was scored, an override was recorded,
 *   a request failed. It fades, so it must never be the only place a
 *   lasting fact is shown. Lasting states stay on the page: the "this
 *   takes a few minutes" panel while an upload runs, the unreliable-score
 *   banners, an empty list that could not load. That split is what keeps
 *   the two from repeating each other.
 *
 * NO DUPLICATES
 *   A notice with a `key` replaces the visible toast with the same key
 *   rather than stacking beside it, and restarts its timer. A notice with
 *   no key is dropped if an identical one is already showing. React's
 *   StrictMode double-runs effects in development, and a server outage
 *   fails every request at once - both would otherwise pile up copies.
 *
 * TIMING
 *   Each kind has a base duration, extended for long messages so they can
 *   be read. The clock pauses while the pointer or keyboard focus is on a
 *   toast, and while the browser tab is hidden: an upload takes many
 *   minutes, a reviewer switches tabs, and a "Scoring completed" toast
 *   that expired unseen in a background tab would be a notification
 *   nobody received.
 */
import {
  createContext, useCallback, useContext, useEffect, useMemo, useReducer,
  useRef, useState,
} from "react";

export const MAX_TOASTS = 4;
export const DURATION = { success: 5000, info: 6000, warning: 8000, error: 10000 };
const MAX_DURATION = 15000;
/** ~25 characters a second, a comfortable reading pace for short notices. */
const MS_PER_EXTRA_CHAR = 40;
const BASE_LENGTH = 90;
const KINDS = new Set(["success", "info", "warning", "error"]);

const ICON = { success: "✓", info: "i", warning: "!", error: "✕" };
const SPOKEN_KIND = {
  success: "Success", info: "Information", warning: "Warning", error: "Error",
};

/** How long a toast stays up, in milliseconds. */
export function toastDuration(kind, message = "") {
  const base = DURATION[kind] ?? DURATION.info;
  const extra = Math.max(0, String(message).length - BASE_LENGTH) * MS_PER_EXTRA_CHAR;
  return Math.min(base + extra, MAX_DURATION);
}

let nextId = 0;

/** Normalise a notice from notices.js into a toast record. */
export function makeToast(notice) {
  const kind = KINDS.has(notice?.kind) ? notice.kind : "info";
  const message = notice?.message || "";
  nextId += 1;
  return {
    id: `toast-${nextId}`,
    key: notice?.key ?? null,
    kind,
    title: notice?.title || "",
    message,
    duration: notice?.duration ?? toastDuration(kind, message),
    version: 0,
  };
}

function isSame(existing, incoming) {
  if (incoming.key) return existing.key === incoming.key;
  return !existing.key
    && existing.kind === incoming.kind
    && existing.title === incoming.title
    && existing.message === incoming.message;
}

/**
 * The toast list as a pure function, so the no-duplicates and
 * size-limit rules can be tested without a browser.
 */
export function toastReducer(toasts, action) {
  switch (action.type) {
    case "show": {
      const incoming = action.toast;
      const match = toasts.find((t) => isSame(t, incoming));
      if (match) {
        // Same slot, new content, fresh timer. The id is kept so React
        // updates the element in place instead of animating a new one in.
        return toasts.map((t) => (t === match
          ? { ...incoming, id: t.id, version: t.version + 1 }
          : t));
      }
      const next = [...toasts, incoming];
      return next.length > MAX_TOASTS ? next.slice(next.length - MAX_TOASTS) : next;
    }
    case "dismiss":
      return toasts.filter((t) => t.id !== action.id);
    default:
      return toasts;
  }
}

// --------------------------------------------------------------------------
// Context
// --------------------------------------------------------------------------

/**
 * Outside a provider, show() does nothing rather than throwing. Components
 * are rendered on their own in verify-ui.mjs, and a missing provider must
 * cost a notification, never the whole page.
 */
const NOOP = { show: () => {}, dismiss: () => {} };
const ToastContext = createContext(NOOP);

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }) {
  const [toasts, dispatch] = useReducer(toastReducer, []);

  // show(null) is a no-op, so a validator that returns null for "fine"
  // can be passed straight through.
  const show = useCallback((notice) => {
    if (!notice) return;
    dispatch({ type: "show", toast: makeToast(notice) });
  }, []);
  const dismiss = useCallback((id) => dispatch({ type: "dismiss", id }), []);
  const api = useMemo(() => ({ show, dismiss }), [show, dismiss]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

// --------------------------------------------------------------------------
// Rendering
// --------------------------------------------------------------------------

function usePageVisible() {
  const [visible, setVisible] = useState(
    () => typeof document === "undefined" || document.visibilityState !== "hidden");
  useEffect(() => {
    const update = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);
  return visible;
}

export function ToastItem({ toast, paused = false, onDismiss }) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const remaining = useRef(toast.duration);
  const startedAt = useRef(0);

  // A replaced toast gets its full time again. Declared before the timer
  // effect so it runs first when the version changes.
  useEffect(() => {
    remaining.current = toast.duration;
  }, [toast.version, toast.duration]);

  const halted = paused || hovered || focused;
  useEffect(() => {
    if (halted) return undefined;
    startedAt.current = Date.now();
    const timer = setTimeout(() => onDismiss(toast.id), Math.max(remaining.current, 0));
    return () => {
      clearTimeout(timer);
      remaining.current -= Date.now() - startedAt.current;
    };
  }, [halted, toast.id, toast.version, onDismiss]);

  const urgent = toast.kind === "error";
  return (
    <div
      className={`toast toast-${toast.kind}`}
      role={urgent ? "alert" : "status"}
      aria-live={urgent ? "assertive" : "polite"}
      aria-atomic="true"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setFocused(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") onDismiss(toast.id);
      }}
    >
      <span className="toast-icon" aria-hidden="true">{ICON[toast.kind]}</span>
      <div className="toast-body">
        <p className="toast-title">
          <span className="visually-hidden">{SPOKEN_KIND[toast.kind]}: </span>
          {toast.title}
        </p>
        {toast.message && <p className="toast-message">{toast.message}</p>}
      </div>
      <button
        type="button"
        className="toast-close"
        aria-label="Dismiss notification"
        onClick={() => onDismiss(toast.id)}
      >
        ×
      </button>
    </div>
  );
}

export function ToastViewport({ toasts, onDismiss }) {
  const visible = usePageVisible();
  return (
    <section className="toast-viewport" aria-label="Notifications">
      {toasts.map((toast) => (
        <ToastItem
          key={toast.id}
          toast={toast}
          paused={!visible}
          onDismiss={onDismiss}
        />
      ))}
    </section>
  );
}
