# Frontend — Wisconsin DOR Chatbot

The Next.js (App Router) web client for the Wisconsin DOR property-tax assistant. It
authenticates against Cognito, opens a WebSocket to API Gateway, and renders the streamed
answer, its source cards, any clarification chips, and the live agent trace.

Deployed as part of the CDK stack via `cdk-nextjs-standalone` onto CloudFront — there is
no Vercel deploy. See the repo root `README.md` and
[`docs/graphrag-engineering-guide.md`](../docs/graphrag-engineering-guide.md) for the
system as a whole.

## Local development

```bash
bun install          # from the repo root
cd frontend
bun dev              # Next.js dev server with Turbopack, http://localhost:3000
```

Create `frontend/.env.local` with the four public values, taken from the deployed stack's
CloudFormation outputs:

```
NEXT_PUBLIC_API_BASE_URL          <- WisconsinBotStack.ApiBaseUrl
NEXT_PUBLIC_WEBSOCKET_URL         <- WisconsinBotStack.WebSocketUrl
NEXT_PUBLIC_USER_POOL_ID          <- WisconsinBotStack.CognitoUserPoolId
NEXT_PUBLIC_USER_POOL_CLIENT_ID   <- WisconsinBotStack.CognitoUserPoolClientId
```

In a CDK deploy these are baked at build time by `infra/stacks/webapp-stack.ts`; the
`.env.local` file is only for running against a deployed backend from your laptop.

## Layout

| Path | What it is |
| --- | --- |
| `src/app/` | Routes: the chat page, `/login`, `/signup`, `/forgot-password`, and `/admin/*` |
| `src/components/` | Chat UI, message rendering, source cards, the agent-trace panel |
| `src/hooks/` | `use-websocket-chat.ts` (the socket + message switch), activity data, dev-trace toggle |
| `src/stores/` | Zustand stores — chat, feedback, settings |
| `src/contexts/` | `auth-context.tsx` (Cognito session, `hasAdminGroup`) |
| `src/api/`, `src/lib/` | HTTP client, auth helpers, link/citation resolution |
| `types/message-types.ts` | **Zod schemas for every WebSocket message** — half of the wire contract |

## Admin routes

`/admin/activity` (tester queries, answers and rich feedback), `/admin/chunks` (inspect a
document's pre-embedding chunks), `/admin/canvas` (visualize one retrieval run), and
`/admin/ingest` (ingest a document by URL).

**All of them require the `Admins` Cognito group**, checked client-side via `hasAdminGroup`
in `auth-context` plus `<ProtectedRoute requireAdmin>` in the admin layout; non-members get
an "Admin access required" panel. Every `/admin/*` HTTP API route is independently gated
server-side by `require_admin()` in `backend/lambdas/chat_api/main.py`. The group is
console-managed today, not defined in CDK.

## The WebSocket contract

The client validates every inbound frame with `WebSocketMessageSchema.parse()`. A shape
the Zod union does not know **drops the whole frame** and surfaces an error to the user, so
a backend message shape and its Zod schema must change together. Both halves and the
handler switch:

1. `backend/layers/websocket_utils/models.py` (Pydantic) + the `send_json` router in `utils.py`
2. `frontend/types/message-types.ts` (Zod discriminated union)
3. the `messageHandler` switch in `src/hooks/use-websocket-chat.ts`

Details, including the `choices` clarification fields and the out-of-band heartbeat:
[`docs/graphrag-engineering-guide.md` § WebSocket Streaming and Contracts](../docs/graphrag-engineering-guide.md#14-websocket-streaming-and-contracts)
and the "WebSocket Contract" section of the repo-root `CLAUDE.md`.

## Tests and lint

```bash
bun test src         # (or `bun run test`) co-located tests under src/**/test/
bunx eslint .        # run from this directory — the repo-root eslint config does not load under ESLint 8
```

Tests live in `test/` directories next to the code they cover. See
[`docs/testing.md`](../docs/testing.md) for the repo-wide test map.
