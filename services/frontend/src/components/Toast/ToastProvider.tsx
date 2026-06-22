// Toast notifications — provider + hook + portal-rendered Toaster.
//
// Framework-light (no toast/animation libs). A context exposes an imperative
// `toast(opts)` API; the Toaster renders a fixed, top-right, stacked region via
// a portal. Each toast auto-dismisses (pause-on-hover / pause-on-focus), is
// keyboard-dismissible, and animates in/out honoring prefers-reduced-motion.
//
// Accessibility:
// - Each toast is its OWN live region: role="status" (polite: success/info) or
//   role="alert" (assertive: error/warning) so assistive tech announces the
//   right urgency. The portal container is a plain wrapper (NO aria-live) — a
//   live-role child nested inside an aria-live region is an anti-pattern that
//   causes double announcements / urgency downgrades, so only one layer is live.
// - Each toast has a focusable Dismiss button (the existing Icon "close" glyph)
//   with an accessible name; the whole toast is keyboard reachable.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { Icon } from "../Icon";
import "./Toast.css";

export type ToastVariant = "success" | "error" | "info" | "warning";

export interface ToastOptions {
  title?: string;
  description?: string;
  variant?: ToastVariant;
  /** Auto-dismiss delay in ms. Use 0 to disable auto-dismiss (sticky). */
  duration?: number;
}

interface ToastRecord extends ToastOptions {
  id: number;
  variant: ToastVariant;
  duration: number;
}

export interface ToastApi {
  toast: (opts: ToastOptions) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

const DEFAULT_DURATION = 5000;

/** Per-variant glyph + the announcement urgency for the live region role. */
const VARIANT_ICON: Record<ToastVariant, "check" | "alert" | "bell"> = {
  success: "check",
  error: "alert",
  warning: "alert",
  info: "bell",
};

function isAssertive(variant: ToastVariant): boolean {
  return variant === "error" || variant === "warning";
}

export interface ToastProviderProps {
  children: ReactNode;
}

export function ToastProvider({ children }: ToastProviderProps): JSX.Element {
  const [toasts, setToasts] = useState<ToastRecord[]>([]);
  const idRef = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback((opts: ToastOptions) => {
    const id = (idRef.current += 1);
    const variant = opts.variant ?? "info";
    const duration = opts.duration ?? DEFAULT_DURATION;
    setToasts((prev) => [...prev, { ...opts, id, variant, duration }]);
  }, []);

  const api = useMemo<ToastApi>(() => ({ toast }), [toast]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <Toaster toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (ctx === null) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return ctx;
}

interface ToasterProps {
  toasts: ToastRecord[];
  onDismiss: (id: number) => void;
}

/** Portal host for the stacked toast region. Rendered to <body>. */
function Toaster({ toasts, onDismiss }: ToasterProps): JSX.Element | null {
  if (typeof document === "undefined") return null;

  return createPortal(
    <div data-toaster>
      <ol data-toast-list>
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />
        ))}
      </ol>
    </div>,
    document.body,
  );
}

interface ToastItemProps {
  toast: ToastRecord;
  onDismiss: (id: number) => void;
}

function ToastItem({ toast, onDismiss }: ToastItemProps): JSX.Element {
  const { id, variant, title, description, duration } = toast;
  const [leaving, setLeaving] = useState(false);
  // Remaining time and the timestamp at which the active timer was (re)started.
  const remainingRef = useRef(duration);
  const startedRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Play the exit animation, then remove. Reduced motion shortens the CSS to
  // ~0ms so this resolves promptly there too.
  const beginDismiss = useCallback(() => {
    setLeaving(true);
  }, []);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const startTimer = useCallback(() => {
    if (duration <= 0) return; // sticky toast: no auto-dismiss
    clearTimer();
    startedRef.current = Date.now();
    timerRef.current = setTimeout(beginDismiss, remainingRef.current);
  }, [duration, beginDismiss, clearTimer]);

  const pauseTimer = useCallback(() => {
    if (duration <= 0 || timerRef.current === null) return;
    clearTimer();
    remainingRef.current -= Date.now() - startedRef.current;
  }, [duration, clearTimer]);

  useEffect(() => {
    startTimer();
    return clearTimer;
  }, [startTimer, clearTimer]);

  // After the leave animation completes, actually drop the toast. We listen for
  // the animationend on the leaving element; a fallback timeout guards against
  // reduced-motion / no-animation environments where the event may not fire.
  const handleAnimationEnd = useCallback(() => {
    if (leaving) onDismiss(id);
  }, [leaving, id, onDismiss]);

  useEffect(() => {
    if (!leaving) return;
    const fallback = setTimeout(() => onDismiss(id), 400);
    return () => clearTimeout(fallback);
  }, [leaving, id, onDismiss]);

  return (
    <li
      data-toast
      data-variant={variant}
      data-leaving={leaving || undefined}
      role={isAssertive(variant) ? "alert" : "status"}
      aria-atomic="true"
      onMouseEnter={pauseTimer}
      onMouseLeave={startTimer}
      onFocus={pauseTimer}
      onBlur={startTimer}
      onAnimationEnd={handleAnimationEnd}
    >
      <span data-toast-icon aria-hidden="true">
        <Icon name={VARIANT_ICON[variant]} size={18} />
      </span>
      <div data-toast-content>
        {title !== undefined && title !== "" ? (
          <p data-toast-title>{title}</p>
        ) : null}
        {description !== undefined && description !== "" ? (
          <p data-toast-description>{description}</p>
        ) : null}
      </div>
      <button
        type="button"
        data-toast-dismiss
        aria-label="Dismiss notification"
        onClick={beginDismiss}
      >
        <Icon name="close" size={16} />
      </button>
    </li>
  );
}
