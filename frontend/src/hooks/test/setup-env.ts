// Side-effect module: must be the FIRST import of any test that pulls in the
// hook/API chain, because `src/lib/http.ts` reads NEXT_PUBLIC_API_BASE_URL at
// module scope and throws when it is unset. ESM evaluates static imports in
// source order, so importing this file first guarantees the var exists before
// `lib/http` is loaded — without depending on the shell environment.
process.env.NEXT_PUBLIC_API_BASE_URL ||= 'http://localhost/test-api';
process.env.NEXT_PUBLIC_WEBSOCKET_URL ||= 'ws://localhost/test-ws';

export {};
