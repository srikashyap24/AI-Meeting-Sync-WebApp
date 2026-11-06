import { useEffect } from "react";

export interface ToastMsg {
  kind: "success" | "error" | "info";
  text: string;
}

export default function Toast({ toast, onClose }: { toast: ToastMsg | null; onClose: () => void }) {
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(onClose, toast.kind === "error" ? 9000 : 5000);
    return () => clearTimeout(t);
  }, [toast, onClose]);
  if (!toast) return null;
  return (
    <div className={`toast ${toast.kind}`} role="status">
      <span>{toast.text}</span>
      <button className="icon-btn" aria-label="Dismiss" onClick={onClose}>×</button>
    </div>
  );
}
