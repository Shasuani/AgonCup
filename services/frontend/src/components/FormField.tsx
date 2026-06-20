// Labeled form controls. FormField wires a <label> to its control via a
// generated id and renders optional hint/error text with aria-describedby.
// TextInput is the common single-line input built on top of it.

import { useId, type InputHTMLAttributes, type ReactNode } from "react";

export interface FormFieldProps {
  label: string;
  /** Render-prop receiving the ids to wire onto the control. */
  children: (ids: { inputId: string; describedBy: string | undefined }) => ReactNode;
  hint?: string;
  error?: string;
  required?: boolean;
}

export function FormField({
  label,
  children,
  hint,
  error,
  required,
}: FormFieldProps): JSX.Element {
  const inputId = useId();
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;
  const describedBy =
    [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;

  return (
    <div data-form-field>
      <label htmlFor={inputId}>
        {label}
        {required ? <span aria-hidden="true"> *</span> : null}
      </label>
      {children({ inputId, describedBy })}
      {hint ? (
        <p id={hintId} data-hint>
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} role="alert" data-field-error>
          {error}
        </p>
      ) : null}
    </div>
  );
}

export interface TextInputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  hint?: string;
  error?: string;
}

export function TextInput({
  label,
  hint,
  error,
  required,
  ...inputProps
}: TextInputProps): JSX.Element {
  return (
    <FormField label={label} hint={hint} error={error} required={required}>
      {({ inputId, describedBy }) => (
        <input
          id={inputId}
          aria-describedby={describedBy}
          aria-invalid={error ? true : undefined}
          required={required}
          {...inputProps}
        />
      )}
    </FormField>
  );
}
