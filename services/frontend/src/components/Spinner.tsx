// Accessible loading indicator. Uses role=status + an aria-label so screen
// readers announce it; visually a simple spinning glyph (no CSS framework).

export interface SpinnerProps {
  label?: string;
}

export function Spinner({ label = "Loading" }: SpinnerProps): JSX.Element {
  return (
    <span role="status" aria-live="polite" aria-label={label} data-spinner>
      <span aria-hidden="true">⏳</span>
    </span>
  );
}
