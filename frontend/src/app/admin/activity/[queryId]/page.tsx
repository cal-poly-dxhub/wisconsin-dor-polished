import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ActivityDetail } from '../_components/activity-detail';

export default async function QueryDetailPage({
  params,
}: {
  params: Promise<{ queryId: string }>;
}) {
  const { queryId } = await params;

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      <Button asChild variant="ghost" size="sm" className="mb-6 gap-1.5 text-xs text-muted-foreground">
        <Link href="/admin/activity">
          <ArrowLeft className="h-3 w-3" />
          Back to activity
        </Link>
      </Button>
      <ActivityDetail queryId={queryId} />
    </div>
  );
}
