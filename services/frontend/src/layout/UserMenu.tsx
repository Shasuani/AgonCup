// Topbar user menu: an avatar (initials) trigger that opens a small menu with
// the signed-in identity and the Sign out action.
//
// INTEGRITY: the displayed identity is derived ONLY from the decoded access-token
// claims (sub) — never fabricated. When there is no sub we fall back to a generic
// role-neutral label ("Operator") and a "—" avatar, never an invented name.
//
// Accessibility: the trigger is a <button aria-haspopup="menu" aria-expanded>; the
// popup is role="menu" with role="menuitem" children. Escape closes and returns
// focus to the trigger; a click outside closes. The Sign out item keeps the
// literal "Sign out" text and the existing logout() handler so behavior is
// preserved exactly.

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { rolesOf } from "../auth/roles";

import "./UserMenu.css";

/** Derive up-to-2 uppercase initials from an identity string. */
function initialsOf(identity: string): string {
  const cleaned = identity.trim();
  if (cleaned.length === 0) return "—";
  const parts = cleaned.split(/[\s._-]+/).filter(Boolean);
  if (parts.length >= 2) {
    const a = parts[0]?.charAt(0) ?? "";
    const b = parts[1]?.charAt(0) ?? "";
    const initials = (a + b).toUpperCase();
    return initials.length > 0 ? initials : "—";
  }
  return cleaned.slice(0, 2).toUpperCase();
}

/** A friendly role label from the highest-privilege role on the token. */
function roleLabel(roles: readonly string[]): string | null {
  if (roles.includes("platform_operator")) return "Platform operator";
  if (roles.includes("guild_admin")) return "Guild admin";
  const first = roles[0];
  if (first) return first.replace(/_/g, " ");
  return null;
}

export function UserMenu(): JSX.Element {
  const { claims, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const menuId = useId();

  const sub = typeof claims?.sub === "string" ? claims.sub : null;
  const identity = sub ?? "Operator";
  const roles = rolesOf(claims);
  const subtitle = roleLabel(roles);

  const close = useCallback(() => setOpen(false), []);

  // Close on outside click + Escape (returning focus to the trigger).
  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: MouseEvent): void {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div data-user-menu ref={containerRef}>
      <button
        type="button"
        ref={triggerRef}
        data-user-menu-trigger
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Account menu for ${identity}`}
        onClick={() => setOpen((v) => !v)}
      >
        <span data-user-avatar aria-hidden="true">
          {initialsOf(identity)}
        </span>
      </button>

      {open ? (
        <div id={menuId} role="menu" data-user-menu-popup aria-label="Account">
          <div data-user-menu-identity>
            <span data-user-menu-name>{identity}</span>
            {subtitle ? <span data-user-menu-role>{subtitle}</span> : null}
          </div>
          <button
            type="button"
            role="menuitem"
            data-user-menu-item
            onClick={() => {
              close();
              logout();
            }}
          >
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}
