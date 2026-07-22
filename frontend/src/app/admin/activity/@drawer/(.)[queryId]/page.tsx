import { ActivityDrawer } from '../../_components/activity-drawer';

export default async function ActivityDrawerPage({
  params,
}: {
  params: Promise<{ queryId: string }>;
}) {
  const { queryId } = await params;
  return <ActivityDrawer queryId={queryId} />;
}
