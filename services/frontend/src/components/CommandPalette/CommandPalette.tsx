// Command palette (Cmd/Ctrl+K).
//
// A framework-light, accessible command launcher. Mount <CommandPaletteProvider>
// inside the router so hosts that register navigation commands can use
// useNavigate; the provider itself has no router dependency. Children can:
//   - call useCommandPalette().open() from a topbar button (or rely on Cmd/Ctrl+K)
//   - register commands via useCommandRegistration([...], deps)
//
// Accessibility: the dialog is role="dialog" + aria-modal, labelled by its
// heading. The text field is an ARIA 1.2 combobox (role="combobox",
// aria-expanded, aria-controls -> a role="listbox", aria-activedescendant ->
// the active role="option"). Arrow keys move the active option, Enter runs it,
// Escape closes, Tab is trapped within the dialog, and focus returns to the
// previously focused element on close. Honors prefers-reduced-motion via CSS.
// No network access.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { Icon } from "../Icon";
import type { Command, CommandPaletteContextValue } from "./types";
import "./CommandPalette.css";

const CommandPaletteContext = createContext<CommandPaletteContextValue | null>(null);

/**
 * Access the palette controls (open/close/toggle/register). Throws when used
 * outside a <CommandPaletteProvider> so wiring mistakes fail loudly.
 */
export function useCommandPalette(): CommandPaletteContextValue {
  const ctx = useContext(CommandPaletteContext);
  if (!ctx) {
    throw new Error("useCommandPalette must be used within a <CommandPaletteProvider>");
  }
  return ctx;
}

/**
 * Declaratively register commands for the lifetime of the calling component.
 * Pass a dependency list (like useEffect) so the set re-registers when it
 * changes. Safe to call only inside a CommandPaletteProvider.
 */
export function useCommandRegistration(
  commands: readonly Command[],
  deps: readonly unknown[],
): void {
  const { register } = useCommandPalette();
  // The command array identity changes every render; gate on the caller-supplied
  // deps instead so we don't churn the registry each render.
  useEffect(() => {
    const unregister = register(commands);
    return unregister;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

// --- filtering -------------------------------------------------------------

function scoreMatch(command: Command, query: string): number {
  // Returns a sortable score (higher = better); -1 means "no match". A simple,
  // dependency-free fuzzy/substring matcher over label + hint + keywords.
  const haystacks = [command.label, command.hint ?? "", ...(command.keywords ?? [])]
    .map((s) => s.toLowerCase())
    .filter(Boolean);
  const q = query.trim().toLowerCase();
  if (q === "") return 0;

  let best = -1;
  for (const hay of haystacks) {
    const idx = hay.indexOf(q);
    if (idx === -1) continue;
    // Prefer earlier matches and matches against the primary label.
    const positional = 100 - Math.min(idx, 99);
    const labelBonus = hay === command.label.toLowerCase() ? 50 : 0;
    best = Math.max(best, positional + labelBonus);
  }
  return best;
}

function filterCommands(commands: readonly Command[], query: string): Command[] {
  if (query.trim() === "") return [...commands];
  return commands
    .map((command) => ({ command, score: scoreMatch(command, query) }))
    .filter((entry) => entry.score >= 0)
    .sort((a, b) => b.score - a.score)
    .map((entry) => entry.command);
}

// --- focus trap helper -----------------------------------------------------

const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

function trapTab(container: HTMLElement, event: KeyboardEvent): void {
  const nodes = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
  if (nodes.length === 0) {
    event.preventDefault();
    return;
  }
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  const active = document.activeElement;
  if (event.shiftKey) {
    if (active === first || !container.contains(active)) {
      event.preventDefault();
      last?.focus();
    }
  } else if (active === last) {
    event.preventDefault();
    first?.focus();
  }
}

// --- provider --------------------------------------------------------------

export interface CommandPaletteProviderProps {
  children: ReactNode;
  /**
   * Placeholder text for the search field. Defaults to a generic prompt; the
   * host can localize or specialize it.
   */
  placeholder?: string;
  /** Shown when no command matches the query. */
  emptyMessage?: string;
}

export function CommandPaletteProvider({
  children,
  placeholder = "Type a command or search…",
  emptyMessage = "No matching commands.",
}: CommandPaletteProviderProps): JSX.Element {
  const [isOpen, setIsOpen] = useState(false);
  // The registry is a map of source-set -> commands. We store registrations in a
  // ref and bump a counter to trigger re-render, so register/unregister never
  // recreates the context value identity (stable open/close/toggle/register).
  const registry = useRef<Map<symbol, readonly Command[]>>(new Map());
  const [registryVersion, setRegistryVersion] = useState(0);

  const open = useCallback(() => setIsOpen(true), []);
  const close = useCallback(() => setIsOpen(false), []);
  const toggle = useCallback(() => setIsOpen((v) => !v), []);

  const register = useCallback((commands: readonly Command[]) => {
    const key = Symbol("command-set");
    registry.current.set(key, commands);
    setRegistryVersion((v) => v + 1);
    return () => {
      registry.current.delete(key);
      setRegistryVersion((v) => v + 1);
    };
  }, []);

  // Flatten + de-duplicate (later registration wins on id collision).
  const commands = useMemo<Command[]>(() => {
    const byId = new Map<string, Command>();
    for (const set of registry.current.values()) {
      for (const command of set) byId.set(command.id, command);
    }
    return [...byId.values()];
    // registryVersion is the intentional invalidation signal for the ref read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [registryVersion]);

  // Global Cmd/Ctrl+K toggle.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const isK = event.key === "k" || event.key === "K";
      if (isK && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        setIsOpen((v) => !v);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const value = useMemo<CommandPaletteContextValue>(
    () => ({ open, close, toggle, isOpen, register }),
    [open, close, toggle, isOpen, register],
  );

  return (
    <CommandPaletteContext.Provider value={value}>
      {children}
      {isOpen ? (
        <CommandPaletteDialog
          commands={commands}
          placeholder={placeholder}
          emptyMessage={emptyMessage}
          onClose={close}
        />
      ) : null}
    </CommandPaletteContext.Provider>
  );
}

// --- dialog ----------------------------------------------------------------

interface CommandPaletteDialogProps {
  commands: readonly Command[];
  placeholder: string;
  emptyMessage: string;
  onClose: () => void;
}

function CommandPaletteDialog({
  commands,
  placeholder,
  emptyMessage,
  onClose,
}: CommandPaletteDialogProps): JSX.Element {
  const titleId = useId();
  const listId = useId();
  const inputId = useId();
  const optionBaseId = useId();

  const dialogRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const previousFocus = useRef<Element | null>(null);

  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);

  const results = useMemo(() => filterCommands(commands, query), [commands, query]);

  // Clamp the active index whenever the result set changes.
  useEffect(() => {
    setActiveIndex((i) => {
      if (results.length === 0) return 0;
      return Math.min(i, results.length - 1);
    });
  }, [results.length]);

  // On open: remember focus, move it into the search field. On close: restore.
  useLayoutEffect(() => {
    previousFocus.current = document.activeElement;
    inputRef.current?.focus();
    return () => {
      const target = previousFocus.current;
      if (target instanceof HTMLElement) target.focus();
    };
  }, []);

  // Keep the active option scrolled into view.
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const active = list.querySelector<HTMLElement>('[aria-selected="true"]');
    // scrollIntoView is unavailable in some environments (e.g. jsdom); guard it.
    if (active && typeof active.scrollIntoView === "function") {
      active.scrollIntoView({ block: "nearest" });
    }
  }, [activeIndex, results.length]);

  const runAt = useCallback(
    (index: number) => {
      const command = results[index];
      if (!command) return;
      // Close first so focus restoration happens, then run the side effect.
      onClose();
      command.run();
    },
    [results, onClose],
  );

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      switch (event.key) {
        case "Escape":
          event.preventDefault();
          onClose();
          break;
        case "ArrowDown":
          event.preventDefault();
          setActiveIndex((i) => (results.length === 0 ? 0 : (i + 1) % results.length));
          break;
        case "ArrowUp":
          event.preventDefault();
          setActiveIndex((i) =>
            results.length === 0 ? 0 : (i - 1 + results.length) % results.length,
          );
          break;
        case "Home":
          if (results.length > 0) {
            event.preventDefault();
            setActiveIndex(0);
          }
          break;
        case "End":
          if (results.length > 0) {
            event.preventDefault();
            setActiveIndex(results.length - 1);
          }
          break;
        case "Enter":
          event.preventDefault();
          runAt(activeIndex);
          break;
        case "Tab":
          // Trap focus inside the dialog (it's a modal).
          if (dialogRef.current) trapTab(dialogRef.current, event.nativeEvent);
          break;
        default:
          break;
      }
    },
    [results.length, activeIndex, runAt, onClose],
  );

  const activeId =
    results.length > 0 ? `${optionBaseId}-${activeIndex}` : undefined;

  const dialog = (
    <div
      data-command-overlay
      onMouseDown={(event) => {
        // Close when the backdrop (not the panel) is pressed.
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-command-palette
        onKeyDown={onKeyDown}
      >
        <h2 id={titleId} data-command-title>
          Command palette
        </h2>
        <div data-command-search>
          <span data-command-search-icon aria-hidden="true">
            <Icon name="search" size={18} />
          </span>
          <input
            ref={inputRef}
            id={inputId}
            type="text"
            role="combobox"
            aria-expanded
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={activeId}
            aria-label="Search commands"
            placeholder={placeholder}
            autoComplete="off"
            spellCheck={false}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActiveIndex(0);
            }}
          />
          <kbd data-command-esc-hint aria-hidden="true">
            Esc
          </kbd>
        </div>

        {results.length === 0 ? (
          <p data-command-empty role="status">
            {emptyMessage}
          </p>
        ) : (
          <ul ref={listRef} id={listId} role="listbox" aria-label="Commands" data-command-list>
            {results.map((command, index) => {
              const selected = index === activeIndex;
              return (
                <li
                  key={command.id}
                  id={`${optionBaseId}-${index}`}
                  role="option"
                  aria-selected={selected}
                  data-command-option
                  // onMouseDown (not onClick) so the run fires before the input
                  // blur/overlay handling and focus restoration is consistent.
                  onMouseDown={(event) => {
                    event.preventDefault();
                    runAt(index);
                  }}
                  onMouseMove={() => setActiveIndex(index)}
                >
                  {command.icon ? (
                    <span data-command-option-icon aria-hidden="true">
                      <Icon name={command.icon} size={18} />
                    </span>
                  ) : (
                    <span data-command-option-icon aria-hidden="true" />
                  )}
                  <span data-command-option-text>
                    <span data-command-option-label>{command.label}</span>
                    {command.hint ? (
                      <span data-command-option-hint>{command.hint}</span>
                    ) : null}
                  </span>
                  {command.group ? (
                    <span data-command-option-group>{command.group}</span>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}

        <footer data-command-footer>
          <span>
            <kbd>↑</kbd>
            <kbd>↓</kbd> to navigate
          </span>
          <span>
            <kbd>↵</kbd> to select
          </span>
          <span>
            <kbd>Esc</kbd> to close
          </span>
        </footer>
      </div>
    </div>
  );

  return createPortal(dialog, document.body);
}
