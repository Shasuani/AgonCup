// Theme context — dark (default) + light, with persistence and OS preference.
//
// The provider applies `data-theme="dark|light"` on the <html> element so the
// token override block in theme.css (`:root[data-theme="light"]`) takes effect
// without any per-component color overrides. Selection order on first mount:
//   1. a previously saved choice in localStorage ("lol-theme")
//   2. the OS `prefers-color-scheme: light` media query
//   3. dark (the product default)
//
// No network IO, no external deps. Safe to mount as the OUTERMOST provider so the
// whole tree — including the login screen — is themed.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import "./theme.css";

export type Theme = "dark" | "light";

export interface ThemeContextValue {
  /** The currently applied theme. */
  theme: Theme;
  /** Explicitly set the theme (persists the choice). */
  setTheme: (theme: Theme) => void;
  /** Flip between dark and light (persists the choice). */
  toggleTheme: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

const STORAGE_KEY = "lol-theme";

/** Read a persisted theme from localStorage, guarding against SSR/blocked storage. */
function readStoredTheme(): Theme | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw === "dark" || raw === "light" ? raw : null;
  } catch {
    return null;
  }
}

/** The OS-level color-scheme preference, defaulting to dark when unknown. */
function prefersLight(): boolean {
  try {
    return window.matchMedia("(prefers-color-scheme: light)").matches;
  } catch {
    return false;
  }
}

/** Resolve the initial theme: stored choice, else OS preference, else dark. */
function resolveInitialTheme(): Theme {
  const stored = readStoredTheme();
  if (stored) {
    return stored;
  }
  return prefersLight() ? "light" : "dark";
}

/** Apply the theme to <html> and keep `color-scheme` in sync for native UI. */
function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.setAttribute("data-theme", theme);
  root.style.colorScheme = theme;
}

export interface ThemeProviderProps {
  children: ReactNode;
  /** Optional explicit initial theme (tests / deep links). Skips storage/OS. */
  initialTheme?: Theme;
}

export function ThemeProvider({
  children,
  initialTheme,
}: ThemeProviderProps): JSX.Element {
  const [theme, setThemeState] = useState<Theme>(
    () => initialTheme ?? resolveInitialTheme(),
  );

  // Reflect the active theme onto <html> on every change.
  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage may be unavailable (private mode / blocked) — ignore.
    }
  }, []);

  const toggleTheme = useCallback(() => {
    setThemeState((current) => {
      const next: Theme = current === "dark" ? "light" : "dark";
      try {
        window.localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // Ignore storage failures; in-memory state still flips.
      }
      return next;
    });
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, setTheme, toggleTheme }),
    [theme, setTheme, toggleTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

/** Access the theme context. Throws when used outside the provider. */
export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme must be used within a <ThemeProvider>");
  }
  return ctx;
}
