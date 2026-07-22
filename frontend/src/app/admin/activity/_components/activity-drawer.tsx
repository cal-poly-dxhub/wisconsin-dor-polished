'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ExternalLink } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { ActivityDetail } from './activity-detail';

export function ActivityDrawer({ queryId }: { queryId: string }) {
  const router = useRouter();

  return (
    <Sheet open onOpenChange={open => { if (!open) router.back(); }}>
      <SheetContent className="p-0">
        <SheetHeader className="shrink-0 border-b border-border px-6 py-4 pr-14">
          <div className="flex items-center justify-between gap-4">
            <div>
              <SheetTitle>Query details</SheetTitle>
              <SheetDescription className="mt-1 text-xs">
                Review the response, feedback, and retrieval trace.
              </SheetDescription>
            </div>
            <Button asChild variant="ghost" size="sm" className="h-8 shrink-0 gap-1.5 text-xs">
              <Link href={`/admin/activity/${queryId}`} target="_blank" rel="noreferrer">
                <ExternalLink className="h-3.5 w-3.5" />
                Full page
              </Link>
            </Button>
          </div>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
          <ActivityDetail queryId={queryId} />
        </div>
      </SheetContent>
    </Sheet>
  );
}
