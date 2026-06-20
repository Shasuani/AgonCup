// Accessible button. Thin wrapper over <button> that adds variant styling and a
// busy state (disables + sets aria-busy). No UI library.

import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "secondary" | "danger";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /** When true the button is disabled and marked aria-busy. */
  busy?: boolean;
  children: ReactNode;
}

export function Button({
  variant = "secondary",
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
      aria-busy={busy || undefined}
      disabled={disabled || busy}
      {...rest}
    >
      {children}
    </button>
  );
}
