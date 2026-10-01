/** @bun */
// Keep first: sets NEXT_PUBLIC_API_BASE_URL before `lib/http` is evaluated.
import './setup-env';
import { describe, test, expect, beforeEach, afterEach, mock } from 'bun:test';
import { renderHook, act, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', { url: 'http://localhost' });
global.document = dom.window.document;
(global as unknown as { window: typeof dom.window }).window = dom.window;
global.navigator = dom.window.navigator;

// ── A fake partysocket WebSocket that records every socket the hook opens ──
type Listener = (e: unknown) => void;
class FakeSocket {
  static all: FakeSocket[] = [];
  url: string | (() => Promise<string>);
  listeners: Record<string, Listener[]> = {};
  closed = false;
  constructor(url: string | (() => Promise<string>)) {
    this.url = url;
    FakeSocket.all.push(this);
  }
  addEventListener(type: string, fn: Listener) {
    (this.listeners[type] ||= []).push(fn);
  }
  removeEventListener(type: string, fn: Listener) {
    this.listeners[type] = (this.listeners[type] || []).filter(f => f !== fn);
  }
  close() {
    this.closed = true;
  }
  /** What the browser does when the network drops: fire `close` to listeners. */
  drop() {
    (this.listeners.close || []).forEach(fn => fn({}));
  }
  async resolvedUrl() {
    return typeof this.url === 'string' ? this.url : await this.url();
  }
}
mock.module('partysocket', () => ({ WebSocket: FakeSocket }));
mock.module('@/lib/auth', () => ({ getIdToken: async () => 'id-token' }));
mock.module('../../components/errors/use-chat-error', () => ({
  useChatError: () => ({ handleError: () => {} }),
}));

// use-websocket-chat.test.tsx mock.module()s this hook, and Bun's module mocks
// are process-wide; the query suffix loads the real file as its own module.
const realHookPath: string = '../use-validated-websocket.ts?real';
const { useValidatedWebSocket } = (await import(
  realHookPath
)) as typeof import('../use-validated-websocket');
const { useChatStore } = await import('../../stores/chat-store');

// Capture the 30 s reconnect-grace timer instead of waiting for it.
let timers: (() => void)[] = [];
const realSetTimeout = globalThis.setTimeout;

function setup() {
  const qc = new QueryClient();
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderHook(
    () => useValidatedWebSocket(() => {}, { urlBase: 'wss://ws.example', onSuccessfulSend: () => {} }),
    { wrapper: Wrapper },
  );
}
const live = () => FakeSocket.all.filter(s => !s.closed);

describe('useValidatedWebSocket session handling', () => {
  beforeEach(() => {
    FakeSocket.all = [];
    timers = [];
    (globalThis as unknown as { setTimeout: unknown }).setTimeout = (fn: () => void, ms: number) => {
      if (ms >= 1000) {
        timers.push(fn);
        return 0;
      }
      return realSetTimeout(fn, ms);
    };
    useChatStore.getState().reset();
  });
  afterEach(() => {
    // Unmount every hook so none stays subscribed to the shared store.
    cleanup();
    globalThis.setTimeout = realSetTimeout;
    useChatStore.getState().reset();
  });

  test('connects to the store session and sends the ID token', async () => {
    const { result } = setup();
    act(() => useChatStore.getState().setSessionId('s1'));
    expect(result.current.sessionId).toBe('s1');
    expect(live()).toHaveLength(1);
    expect(await live()[0].resolvedUrl()).toBe('wss://ws.example?sessionId=s1&token=id-token');
  });

  test('new chat drops the session at once and closes the socket', () => {
    const { result } = setup();
    act(() => useChatStore.getState().setSessionId('s1'));
    act(() => useChatStore.getState().reset());
    expect(result.current.sessionId).toBeNull();
    expect(live()).toHaveLength(0);
  });

  test('selecting another session moves the socket to it', async () => {
    const { result } = setup();
    act(() => useChatStore.getState().setSessionId('s1'));
    act(() => useChatStore.getState().setSessionId('s2'));
    expect(result.current.sessionId).toBe('s2');
    expect(live()).toHaveLength(1);
    expect(await live()[0].resolvedUrl()).toContain('sessionId=s2');
  });

  test('a network drop keeps the session through the grace period', () => {
    const { result } = setup();
    act(() => useChatStore.getState().setSessionId('s1'));
    act(() => live()[0].drop());
    expect(result.current.sessionId).toBe('s1');
    expect(timers).toHaveLength(1);
    act(() => timers[0]());
    expect(result.current.sessionId).toBeNull();
  });

  test('the grace timer never clears a session adopted after the drop', () => {
    const { result } = setup();
    act(() => useChatStore.getState().setSessionId('s1'));
    act(() => live()[0].drop());
    act(() => useChatStore.getState().setSessionId('s2'));
    timers.forEach(fn => act(() => fn()));
    expect(result.current.sessionId).toBe('s2');
  });
});
