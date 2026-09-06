'use client';

import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { ActivityDetail } from './activity-detail';

// Rendered by the `@drawer` intercepting route when a query row is clicked.
// Despite the slot name this is a full-screen overlay (not a side drawer): it
// fades in/out rather than sliding, and the list page stays mounted behind it,
// so the X / Esc / overlay click closes via router.back() instantly and
// refetches nothing. Built on Radix Dialog directly (not the shared Sheet)
// so the fade animation stays isolated from other Sheet usages.
export function ActivityDrawer({ queryId }: { queryId: string }) {
  const router = useRouter();

  return (
    <DialogPrimitive.Root open onOpenChange={open => { if (!open) router.back(); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40 duration-200 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0" />
        <DialogPrimitive.Content
          className="fixed inset-0 z-50 flex flex-col bg-background duration-200 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0"
        >
          <div className="shrink-0 border-b border-border px-6 py-4 pr-14">
            <DialogPrimitive.Title className="text-sm font-semibold leading-none tracking-tight">
              Query details
            </DialogPrimitive.Title>
            <DialogPrimitive.Description className="mt-1 text-xs text-muted-foreground">
              Review the response, feedback, and retrieval trace.
            </DialogPrimitive.Description>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
            <ActivityDetail queryId={queryId} layout="split" />
          </div>

          <DialogPrimitive.Close className="absolute right-4 top-4 z-10 rounded-sm p-1 text-muted-foreground opacity-80 ring-offset-background transition-opacity hover:bg-accent hover:text-foreground hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2">
            <X className="h-4 w-4" />
            <span className="sr-only">Close</span>
          </DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
