import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Check, CircleAlert, Info, X } from 'lucide-react';

type Tone = 'success' | 'error' | 'info';
type Notice = { id: number; message: string; tone: Tone };
type ToastApi = Record<Tone, (message: string) => void> & { dismiss: (message?: string) => void };

const ToastContext = createContext<ToastApi | null>(null);

// One notice at a time, top centre. Errors stay until dismissed; others time out
// (paused while hovered), as in MethodMark.
export function ToastProvider({ children }: { children: ReactNode }) {
  const [notice, setNotice] = useState<Notice | null>(null);
  const sequence = useRef(0);
  const dismiss = useCallback((message?: string) => {
    setNotice(current => (!message || current?.message === message ? null : current));
  }, []);
  const toast = useMemo<ToastApi>(() => {
    const show = (tone: Tone, message: string) => {
      if (!message.trim()) return;
      setNotice(current => tone === 'error' && current?.message === message ? current
        : { id: ++sequence.current, message, tone });
    };
    return { dismiss, success: m => show('success', m), error: m => show('error', m), info: m => show('info', m) };
  }, [dismiss]);

  return <ToastContext.Provider value={toast}>
    {children}
    {notice && <Toast key={notice.id} notice={notice} onDismiss={() => dismiss()} />}
  </ToastContext.Provider>;
}

export function useToast() {
  const toast = useContext(ToastContext);
  if (!toast) throw new Error('useToast must be used within ToastProvider.');
  return toast;
}

export function useToastError(message: string) {
  const toast = useToast();
  useEffect(() => {
    if (!message) return;
    toast.error(message);
    return () => toast.dismiss(message);
  }, [message, toast]);
}

function Toast({ notice, onDismiss }: { notice: Notice; onDismiss: () => void }) {
  const [hovered, setHovered] = useState(false);
  useEffect(() => {
    if (notice.tone === 'error' || hovered) return;
    const timer = window.setTimeout(onDismiss, notice.tone === 'success' ? 4500 : 8000);
    return () => window.clearTimeout(timer);
  }, [notice.tone, hovered, onDismiss]);
  const Icon = notice.tone === 'error' ? CircleAlert : notice.tone === 'success' ? Check : Info;
  return <aside className="toast-viewport" aria-label="Notifications">
    <div className={`app-toast ${notice.tone}`} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      <span className="toast-icon" aria-hidden="true"><Icon size={17} /></span>
      <p role={notice.tone === 'error' ? 'alert' : 'status'}>{notice.message}</p>
      <button type="button" aria-label="Dismiss notification" onClick={onDismiss}><X size={16} /></button>
    </div>
  </aside>;
}
