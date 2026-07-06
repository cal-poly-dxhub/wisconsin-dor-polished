import { notFound } from 'next/navigation';

export default function VisualizerLayout({ children }: { children: React.ReactNode }) {
  if (!process.env.ENABLE_VISUALIZER) {
    notFound();
  }

  return (
    <div className="flex flex-col h-screen bg-background text-foreground overflow-hidden">
      <main className="flex-1 overflow-hidden">
        {children}
      </main>
    </div>
  );
}
