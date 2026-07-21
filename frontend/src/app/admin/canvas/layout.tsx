import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Retrieval Canvas',
};

// Full-bleed: escape the admin sidebar frame so the visualizer fills the
// viewport. Auth is still enforced by the parent /admin ProtectedRoute layout.
export default function CanvasLayout({ children }: { children: React.ReactNode }) {
  return <div className="fixed inset-0 z-40 bg-background">{children}</div>;
}
