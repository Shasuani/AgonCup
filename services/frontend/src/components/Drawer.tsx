// Slide-over panel anchored to the right edge — the detail view for a row in a
// long table. A detail rendered BELOW a 24-row table is off-screen the moment
// it opens; a drawer arrives next to the row that was clicked and leaves the
// list where it was.
//
// Behaviour: portaled to <body> (so a screen's enter animation can never clip
// it), Escape and the scrim close it, body scroll is locked while open (via a
// `data-scroll-locked` attribute global.css styles), focus moves into the panel
// on open and returns to whatever opened it on close. role="dialog" +
// aria-modal, labelled by the heading the caller names.

import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  /** Id of the heading that names the panel (aria-labelledby). */
  labelledBy: string;
  children: ReactNode;
}

export function Drawer({ open, onClose, labelledBy, children }: DrawerProps): JSX.Element | null {
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  // Latest onClose without making it an effect dependency: a fresh closure per
  // render must not re-run the open/focus effect (which would steal focus
  // back to the panel on every keystroke inside it).
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;
    document.body.setAttribute("data-scroll-locked", "");
    panelRef.current?.focus();
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.removeAttribute("data-scroll-locked");
      const restore = restoreRef.current;
      if (restore && typeof restore.focus === "function" && restore.isConnected) {
        restore.focus();
      }
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div data-drawer-overlay onClick={() => onCloseRef.current()}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        data-drawer
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
