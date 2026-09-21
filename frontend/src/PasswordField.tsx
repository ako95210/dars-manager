import { useState } from "react";

export function PasswordField({
  label,
  value,
  onChange,
  autoComplete = "new-password",
  minLength,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete?: string;
  minLength?: number;
}) {
  const [visible, setVisible] = useState(false);
  return <label className="password-field">{label}<span className="password-input-wrap"><input autoComplete={autoComplete} maxLength={256} minLength={minLength} required type={visible ? "text" : "password"} value={value} onChange={(event) => onChange(event.target.value)} /><button aria-label={visible ? `Masquer ${label.toLowerCase()}` : `Afficher ${label.toLowerCase()}`} aria-pressed={visible} onClick={() => setVisible((current) => !current)} type="button">{visible ? "Masquer" : "Afficher"}</button></span></label>;
}
