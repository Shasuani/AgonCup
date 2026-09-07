// Accessible button. Thin wrapper over <button> that adds variant styling and a
// busy state (disables + sets aria-busy). No UI library.
//
// Variants: primary (the one action on a screen), secondary (everything else),
// danger (destructive), ghost (quiet — transparent until hovered, for row
// actions and toolbars). `size="sm"` is the 30px control used inside table
// rows and the topbar so those stay bars rather than becoming toolbars.

import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";
export type ButtonSize = "sm";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /** Compact 30px control (table rows, topbar). Default is the 36px control. */
  size?: ButtonSize;
  /** When true the button is disabled and marked aria-busy. */
  busy?: boolean;
  children: ReactNode;
}

export function Button({
  variant = "secondary",
  size,
  busy = false,
  disabled,
  children,
  type,
  ...rest
}: ButtonProps): JSX.Element {
  return (
    <button
      type={type ?? "button"}
      data-variant={variant}
      data-size={size}
      aria-busy={busy || undefined}
      disabled={disabled || busy}
      {...rest}
    >
      {children}
    </button>
  );
}
