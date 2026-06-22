// Accessible dark/light theme toggle.
//
// Renders the existing <Button> (variant "secondary", so it picks up the shared
// focus-visible ring + button styling) with a sun/moon glyph from the shared
// <Icon> set. The control is a toggle: `aria-pressed` reflects whether LIGHT mode
// is active, and `aria-label` always states the action the press will perform.
//
// The icon shown is the DESTINATION theme's glyph (moon while in light mode, sun
// while in dark mode) so it reads as "switch to …". Decorative only — the
// accessible name comes from aria-label, never the icon.

import { Button, type ButtonProps } from "../components/Button";
import { Icon } from "../components/Icon";
import { useTheme } from "./ThemeProvider";

export type ThemeToggleProps = Omit<
  ButtonProps,
  "children" | "onClick" | "aria-pressed" | "aria-label" | "variant"
>;

export function ThemeToggle(props: ThemeToggleProps): JSX.Element {
  const { theme, toggleTheme } = useTheme();
  const isLight = theme === "light";
  const label = isLight ? "Switch to dark theme" : "Switch to light theme";

  return (
    <Button
      {...props}
      variant="secondary"
      data-theme-toggle
      aria-label={label}
      aria-pressed={isLight}
      onClick={toggleTheme}
    >
      <Icon name={isLight ? "moon" : "sun"} size={18} />
    </Button>
  );
}
