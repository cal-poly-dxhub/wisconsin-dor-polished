'use client';

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Switch } from '@/components/ui/switch';
import { useSettingsStore, type Persona } from '@/stores/settings-store';

interface SettingsModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SettingsModal({ open, onOpenChange }: SettingsModalProps) {
  const detailedTrace = useSettingsStore((s) => s.detailedTrace);
  const setDetailedTrace = useSettingsStore((s) => s.setDetailedTrace);
  const autoScroll = useSettingsStore((s) => s.autoScroll);
  const setAutoScroll = useSettingsStore((s) => s.setAutoScroll);
  const persona = useSettingsStore((s) => s.persona);
  const setPersona = useSettingsStore((s) => s.setPersona);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>Customize your chat experience.</DialogDescription>
        </DialogHeader>

        <div className="mt-4 space-y-6">
          <div className="flex items-center justify-between gap-4">
            <div className="space-y-0.5">
              <label htmlFor="auto-scroll" className="text-sm font-medium">
                Auto-scroll during responses
              </label>
              <p className="text-xs text-muted-foreground">
                Automatically scroll to the bottom as new content streams in.
              </p>
            </div>
            <Switch
              id="auto-scroll"
              checked={autoScroll}
              onCheckedChange={setAutoScroll}
            />
          </div>

          <div className="flex items-center justify-between gap-4">
            <div className="space-y-0.5">
              <label htmlFor="detailed-trace" className="text-sm font-medium">
                Detailed agent trace
              </label>
              <p className="text-xs text-muted-foreground">
                Show expanded tool calls and reasoning steps while the agent is thinking.
              </p>
            </div>
            <Switch
              id="detailed-trace"
              checked={detailedTrace}
              onCheckedChange={setDetailedTrace}
            />
          </div>

          <div className="space-y-2">
            <div className="space-y-0.5">
              <label className="text-sm font-medium">I am a...</label>
              <p className="text-xs text-muted-foreground">
                Answers are framed differently depending on your role.
              </p>
            </div>
            <div className="flex gap-2">
              {([
                { value: 'citizen' as Persona, label: 'Property owner / taxpayer' },
                { value: 'government' as Persona, label: 'Government worker' },
              ]).map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setPersona(opt.value)}
                  className={`flex-1 rounded-md border px-3 py-2 text-xs font-medium transition-colors ${
                    persona === opt.value
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-border text-muted-foreground hover:border-primary/50'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
