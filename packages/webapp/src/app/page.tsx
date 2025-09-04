'use client';

import { NarrowApp } from '@/components/layout/narrow-app';
import { WideApp } from '@/components/layout/wide-app';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

export default function App() {
  const breakpoint = useBreakpoint();
  const queryClient = new QueryClient();

  return (
    <QueryClientProvider client={queryClient}>
      {breakpoint === 'wide' ? <WideApp /> : <NarrowApp />}
    </QueryClientProvider>
  );
}
