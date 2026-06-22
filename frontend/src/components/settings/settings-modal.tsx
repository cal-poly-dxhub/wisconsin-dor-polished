'use client';

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Switch } from '@/components/ui/switch';
import { useSettingsStore } from '@/stores/settings-store';

interface SettingsModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function SettingsModal({ open, onOpenChange }: SettingsModalProps) {
  const detailedTrace = useSettingsStore((s) => s.detailedTrace);
  const setDetailedTrace = useSettingsStore((s) => s.setDetailedTrace);
  const autoScroll = useSettingsStore((s) => s.autoScroll);
  const setAutoScroll = useSettingsStore((s) => s.setAutoScroll);

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
        </div>
      </DialogContent>
    </Dialog>
  );
}
