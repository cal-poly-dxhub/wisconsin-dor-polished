'use client';

import { NarrowApp } from '@/components/layout/narrow-app';
import { WideApp } from '@/components/layout/wide-app';
import { useBreakpoint } from '@/hooks/use-breakpoint';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ChatErrorProvider } from '@/components/errors/use-chat-error';

const queryClient = new QueryClient();

export default function App() {
  const breakpoint = useBreakpoint();

  return (
    <>
      <script src="http://localhost:8097"></script>
      <ChatErrorProvider>
        <QueryClientProvider client={queryClient}>
          {breakpoint === 'wide' ? <WideApp /> : <NarrowApp />}
        </QueryClientProvider>
      </ChatErrorProvider>
    </>
  );
}
