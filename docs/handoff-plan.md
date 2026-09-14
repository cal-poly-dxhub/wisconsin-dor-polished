# Handoff Plan — Moving the Chatbot into DOR's AWS Account

**Status:** Draft for engineering review (Task 66; folds in Task 63).
**Executors:** Isaac (DxHub), Darren (DxHub). **Reviewers:** Amy (DOR Technology Services), Brad (DOR applications / AI POCs), DOR ISO.
**Source of the ask:** 2026-09-10 DOR meeting. Valeah: "no matter what the cost is, I want exactly what is there right now over into our account … full access for our division … 117 people." Darren committed to SSO via Cognito, a security assessment ("threat models … vulnerabilities … run it through some tests"), a revisited cost estimate, and a maintenance picture of ~20–25% FTE for data-only upkeep.
**Targets:** testing wraps end of September (final DOR meeting Sep 24); operational in DOR's account early October.

Every infra claim below was read from the CDK sources on this branch (`infra/bin/wisconsin-app.ts`, `infra/stacks/*.ts`) or from a read-only inspection of the running us-east-1 stack on 2026-09-14. Where the two disagree it is called out.

---

## 1. Inventory — what moves and how

The whole system is one CloudFormation root stack, `WisconsinBotStack` (`infra/stacks/stack.ts`, name overridden by `-c stackName=WisconsinBotGraphRAG`), containing seven nested stacks. `infra/bin/wisconsin-app.ts` leaves `env` unset, so the template is account-agnostic — it deploys into any account/region the CLI profile points at. That is the good news: **the compute and wiring are recreated by one `cdk deploy`; only the data has to be copied.**

### 1a. CDK-managed (recreated by `cdk deploy`)

| Nested stack (file) | Contains | Notes for the new account |
|---|---|---|
| `LambdaLayersStack` (`lambda-layers-stack.ts`) | Two Python 3.12 layers: `step_function_types`, `websocket_utils` | Pure build artifacts. Docker bundling at synth time — the deploying machine needs Docker. |
| `GraphRAGStack` (`graphrag-stack.ts`) | **S3**: raw bucket (RETAIN), work bucket (DESTROY + autoDelete), FAQ bucket (RETAIN) — names are `wis-{raw,work,faq-bucket-graphrag}-<stack-uid>`, so the new account gets **new bucket names**. **DynamoDB**: `FaqUrlTable`, `ModelConfigTable`. **Bedrock Knowledge Base** `wis-faq-graphrag` (Titan Embed v2, `@cdklabs/generative-ai-cdk-constructs` `VectorKnowledgeBase`) — this construct silently provisions an **OpenSearch Serverless collection** (confirmed live: VECTORSEARCH type, standby replicas ENABLED). **Neptune Analytics** graph `wis-dor-graphrag`, 32 m-NCU, 1024-dim vectors, `publicConnectivity: true`, `replicaCount: 0`, `deletionProtection: false`. | Bucket names change → every script/doc that hardcodes `wis-raw-bucket-c8e69250` (CLAUDE.md, `run_fargate.sh`, ops scripts) must read the new names from stack outputs. Neptune is created **empty**. |
| `SessionsStack` (`sessions-stack.ts`) | **Cognito** user pool `wisconsin-user-pool` (`selfSignUpEnabled: true`, email sign-in, password policy, no MFA, `RemovalPolicy.DESTROY`) + app client (USER_PASSWORD + SRP flows, no secret). **DynamoDB**: `SessionTable` (GSIs `connectionId`, `userIdIndex`), `ChatHistoryTable` (GSIs `sessionIdKey`, `timestampIndex`, `activityIndexV3`). **Lambdas**: `ApiHandler` (chat_api), `ConnectHandler`, `DisconnectHandler`, `DefaultHandler`. **API Gateway v2**: HTTP API (stage `dev`, CORS `allowOrigins: ['*']`, Cognito JWT authorizer on every route incl. `/admin/*`) and WebSocket API (stage `dev`, `$connect`/`$disconnect`/`$default`, **no authorizer**). Log group `/aws/apigateway/websocket-execution-logs` (1 week). | The `Admins` Cognito group that gates `/admin/*` server-side (`backend/lambdas/chat_api/main.py` `require_admin()`) is **not in CDK** — it was created by hand (live: 1 group, 2 members). Must be recreated manually or added to CDK. |
| `GraphRAGMessagesStack` (`graphrag-messages-stack.ts`) | `AgenticRetrievalFunction` (Python 3.12, 512 MB, 120 s, X-Ray on, env from `config/retrieval.toml` via `infra/lib/retrieval-config.ts`). **EventBridge** rule `TriggerGraphRAGMessageProcessing` (source `wisconsin-dor.chat-api`, detail-type `ChatMessageReceived`) → Lambda. | Stateless. Reads prompts from `ModelConfigTable` at cold start. |
| `IngestionStack` (`ingestion-stack.ts`) | **VPC** (2 AZ, public subnets only, 0 NAT), **ECS** cluster `wis-dor-ingestion`, **ECR** repo `wis-dor-ingestion` (keep 5 images), Fargate task def (2 vCPU / 8 GB, image tag `latest`), task role, security group (all outbound), log group `/ecs/wis-dor-ingestion` (30 d). | ECR is created **empty** — rebuild and push (`tools/ingestion/docker/build_and_push.sh`). The task role and container env reference a **legacy Textract staging bucket in a different account** (`textract-chunk-result-dhgoel`); see §3. |
| `CloudWatchIam` (`cloudwatch-iam.ts`) | IAM role `ApiGatewayCloudWatchLogsRoleTest` for API GW logging; `CfnAccount` only if `RESET_ClOUDWATCH_IAM_ROLE` is flipped to true. | Fresh account has no API GW account-level logging role → set the flag `true` for the first deploy, then back to `false`. |
| `WebAppStack` (`webapp-stack.ts`) | `cdk-nextjs-standalone` `Nextjs` construct → **CloudFront** distribution, server Lambda, image Lambda, static-assets bucket, revalidation queue/table. `NEXT_PUBLIC_*` env baked at build. Optional **ACM cert + Route53** alias when `-c domainName/hostedZoneName/hostedZoneId` are passed. | Live stack has **no custom domain, no WAF** (CloudFront alias empty, `wafv2 list-web-acls` empty). DOR will want a `revenue.wi.gov`-adjacent hostname → they supply the hosted zone; the stack already supports it. |

Also managed by CDK bootstrap, not the app: the `CDKToolkit` stack (`cdk bootstrap` in the new account, first step). The live account also has an orphan `SSTConsole-*` stack and ~20 orphan log groups from the deleted legacy `WisconsinBotStack` — do **not** carry those over.

### 1b. Non-CDK state (must be copied, seeded, or configured by hand)

| Item | Live size | How it gets into the new account |
|---|---|---|
| Raw bucket (`raw/`, `worksheets/`, `flowcharts/`, case-law text) | 5,924 objects / 0.33 GB | `aws s3 sync s3://<old-raw> s3://<new-raw>` (cross-account: grant the DOR deploy role read on the old bucket, or push from the DxHub side with a bucket policy on the new one). |
| Work bucket (`extracted/`, `embedded/`, `classified/`, `aliases/`, staging caches) | 19,200 objects / 2.14 GB | Same `s3 sync`. This is the expensive-to-regenerate cache (LLM classification + Nova aliases + Titan embeddings); copying it makes the Neptune load a no-Bedrock-cost operation. |
| FAQ bucket | 1,244 objects / 0.5 MB | `s3 sync`, then **trigger a KB ingestion job** on the new data source (`tools/ingestion/scripts/sync_faq_bucket.sh` does both). |
| Neptune graph | ~2,364 docs, ~9.6k vectors | **Not** a snapshot copy across accounts. Run a full load from the copied work bucket: scale to 128 m-NCU, `run_fargate.sh load`, scale back to 32 (`docs/annual-refresh-runbook.md` has the exact sequence). ~1 h wall clock. |
| `ModelConfigTable` (prompts) | 5 items | `AWS_PROFILE=<your-profile> python tools/upload_model_configs.py` (auto-discovers the table from stack outputs). Source of truth is `config/model_configs.toml`. |
| `FaqUrlTable` | 633 items | `python tools/ingestion/ops/seed_faq_url_table.py --table <FaqUrlTableName> --faqs documents/faqs.json`. |
| `ChatHistoryTable` / `SessionTable` | 1,039 queries / 688 sessions | **Recommend not migrating.** They are pilot-tester data keyed to Cognito `sub`s that will not exist under SSO; DOR should decide retention (§3). If DOR wants the pilot Q&A for analysis, export once with `aws dynamodb scan` to S3/CSV and hand it over out-of-band. |
| Cognito users | 38 users, 2 in `Admins` | **Not migrated** — SSO replaces them (§2). Re-create the `Admins` group (or its SSO-claim equivalent). |
| Bedrock model access | — | Manual console step in DOR's account: enable Anthropic Claude Sonnet 4.6, Claude Haiku 4.5, Amazon Nova 2 Lite, Titan Embed Text v2, and (extract.py doc classification) Claude Sonnet 4. Verify cross-region inference profile IDs with `aws bedrock list-inference-profiles` — the code uses `us.anthropic.claude-sonnet-4-6`, `us.anthropic.claude-haiku-4-5-20251001-v1:0`, `us.amazon.nova-2-lite-v1:0`, `amazon.titan-embed-text-v2:0`. Ask the AWS account manager to raise Bedrock TPM quotas before a 117-user rollout. |
| ECR image | 6 images | `cd tools/ingestion/docker && ./build_and_push.sh` against the new repo URI. |
| Neptune provisioned memory | 32 (floor) | Set by CDK; 16 was rejected by the service ("storage memory constraints", 2026-09-13), so 32 is the resting tier in the new account too. |
| Route53 / ACM | none today | Only if DOR provides a domain; pass the three `-c` context values. |
| Frontend `NEXT_PUBLIC_*` values | baked at build | Automatic — `webapp-stack.ts` wires them from the sessions stack outputs during synth. |

**Order of operations for the data move:** buckets → `cdk deploy` (creates empty graph, tables, KB) → seed the two tables → KB ingestion → ECR push → Neptune full load → smoke test with `tools/ingestion/ops/run_graph_regression.py` pointed at the new graph.

---

## 2. Single sign-on

### What exists today

`sessions-stack.ts` creates a self-contained Cognito pool: email/password, self-signup open, email verification, no MFA, no hosted-UI domain, no federated identity providers (live check: `list-identity-providers` → empty; app client callback URL is the placeholder `https://example.com`). The frontend talks to Cognito directly with `amazon-cognito-identity-js` (`frontend/src/lib/auth.ts`: `signUp`, `confirmSignUp`, `signIn` via SRP, `forgotPassword`, `confirmForgotPassword`, `resendConfirmationCode`), wrapped by `frontend/src/contexts/auth-context.tsx`, with pages `/login`, `/signup`, `/forgot-password` (PR #25). The HTTP API validates the Cognito ID token with `HttpJwtAuthorizer`; `chat_api` reads `sub`, `email`, and `cognito:groups` from the claims.

### Options

| | SAML 2.0 federation | OIDC federation |
|---|---|---|
| Fits | Entra ID (Azure AD), Okta, ADFS, Ping — anything the State's IdP already exposes | Entra ID / Okta also do OIDC; fewer moving parts (no metadata XML) |
| Cognito construct | `cognito.UserPoolIdentityProviderSaml` + `metadata` URL/file | `cognito.UserPoolIdentityProviderOidc` + issuer/client id/secret |
| Group → authorization | Map the IdP group claim to a custom attribute via `attributeMapping`, or use a Cognito **pre-token-generation Lambda** to inject `cognito:groups` so `require_admin()` keeps working unchanged | Same |
| Token still Cognito-issued | Yes — the API authorizer and `chat_api` claim parsing do **not** change | Yes |

Either way the pool stays as a **federation broker**: DOR's IdP authenticates, Cognito issues the JWT the backend already expects. Recommend OIDC if DOR is on Entra ID (most Wisconsin agencies are); SAML if their IdM team only issues SAML apps.

### Hosted UI vs custom login

Federation in Cognito requires the **OAuth authorization-code flow through a Cognito domain** (hosted UI). The current `amazon-cognito-identity-js` SRP flow cannot initiate a federated login. Decision: use the hosted UI with the IdP as the only provider (`supportedIdentityProviders: [idp]`, `COGNITO` removed), which sends the user straight to the State login page — no Cognito-branded screen is ever seen. This is the lowest-effort path and removes the custom login/signup/forgot-password pages entirely.

### Changes

`infra/stacks/sessions-stack.ts`
- Add `userPool.addDomain(...)` (Cognito prefix domain is fine; custom domain needs an ACM cert in us-east-1).
- Add the IdP construct; set `attributeMapping` for `email` and the group claim.
- App client: `oAuth: { flows: { authorizationCodeGrant: true }, scopes: [OPENID, EMAIL, PROFILE], callbackUrls: [<CloudFront URL>/…], logoutUrls }`, `supportedIdentityProviders: [idp]`, drop `authFlows.userPassword`.
- `selfSignUpEnabled: false`, `RemovalPolicy.RETAIN` on the pool.
- Add `CfnUserPoolGroup 'Admins'` (or replace the group check with an IdP claim) so the admin gate is in code, not a console click.
- Optional but recommended: a `preTokenGeneration` trigger that copies the IdP group claim into `cognito:groups`.

Frontend
- Replace `lib/auth.ts` with an Amplify `Auth` / OAuth-redirect implementation (`aws-amplify` 6 is already in `package.json`); `signInWithRedirect` + `fetchAuthSession` for the token. `getIdToken()` is the only function the rest of the app (`lib/http.ts`, the WebSocket hook) depends on — keep its signature.
- Delete `/signup`, `/forgot-password`, and the password fields on `/login` (login becomes a single "Sign in with State of Wisconsin" button, or an auto-redirect). `ProtectedRoute` stays.
- The webapp stack needs the Cognito domain and client id exported as `NEXT_PUBLIC_*`.

### Questions for Amy / Brad (blocks the infra work)

1. IdP product and protocol they will issue (Entra ID OIDC? SAML metadata URL?). Who owns the app registration on their side, and what redirect URIs do they need from us?
2. Which claim carries group membership, and the exact group names for (a) all SLF division users, (b) admins. Do they want the 117 users scoped by group, or is "anyone who can log in to the IdP" acceptable?
3. MFA: enforced at the IdP (then Cognito `mfa: OFF` is correct) or do they expect Cognito to enforce it?
4. Session lifetime policy (the app client currently issues 30-day refresh tokens).
5. Does the ISO require the app be reachable only from the State network / VPN, or is public CloudFront with SSO acceptable?

### Effort

IdP details in hand: **1.5–2 engineer-days** (0.5 CDK, 0.5–1 frontend, 0.5 test with a DOR test account). Add 2–5 elapsed days of back-and-forth with DOR's IdM team for the app registration. Without their details nothing can start beyond stubbing the CDK constructs.

---

## 3. Security assessment scope

A checklist for Darren's specialist engineer. Findings marked **[F#]** are the ones that need a decision before a 117-user rollout; they are ordered by risk.

### Identity and access

- **[F1] Open self-registration on a public URL.** `sessions-stack.ts` sets `selfSignUpEnabled: true`, the frontend ships a `/signup` page, and the pool has no email-domain restriction; live `AllowAdminCreateUserOnly` is false. Anyone who finds the CloudFront URL can create an account with any verified email, then use the API (Bedrock spend) and read the corpus. *Fix:* SSO (§2) eliminates this; as an interim, set `selfSignUpEnabled: false` and pre-create users.
- **[F2] WebSocket `$connect` has no authorizer.** `sessions-stack.ts` adds `$connect` with only a Lambda integration; `backend/lambdas/websocket/connect.py` binds `connectionId` to whatever `sessionId` is in the query string (conditional on the session existing). A session id is a UUID, so brute force is impractical, but any party who learns one (logs, shared links — the app uses URL-based session routing) can attach and receive the streamed answers. *Fix:* add a Lambda request authorizer on `$connect` that validates the Cognito JWT (`?token=`) and checks `session.userId == sub`.
- **[F3] Session-ownership checks are incomplete (IDOR).** `chat_api/main.py`: `update_session_handler` and `delete_session_handler` compare `userId` to the JWT `sub`, but `send_message_handler`, `get_session_history_handler`, and `feedback_handler` only call `validate_session_exists`. Any authenticated user with another user's session UUID can read their history or post queries into it. *Fix:* factor the ownership check into `validate_session_exists` (or a `require_session_owner`).
- **[F4] Admin surface is in the production build, gated only by a hand-made group.** `/admin/{activity,ingest,chunks,canvas}` (`frontend/src/app/admin/layout.tsx`) is protected by `ProtectedRoute` = *any* authenticated user; the server-side `require_admin()` checks `cognito:groups` contains `Admins`, which is not in CDK. CLAUDE.md's "dev-only" is not enforced by any `NODE_ENV` check (`use-dev-trace.ts` only auto-enables the trace panel in dev). The activity page exposes **every user's queries, answers and emails**; `/admin/ingest` accepts arbitrary URLs, downloads them into the raw bucket, and launches a Fargate task (`ecs:RunTask` + `iam:PassRole` granted in `stack.ts`). *Decision for DOR:* (a) keep admin for a named DOR group (put the group in CDK, add a client-side group check so the sidebar link is hidden), or (b) remove `/admin/*` routes and the five `/admin/*` API routes from the production build. Either way the ingest endpoint should require a second gate or be dropped in favor of the runbook.
- MFA is OFF at the pool; acceptable only if the IdP enforces it.
- CORS `allowOrigins: ['*']` on the HTTP API — tighten to the CloudFront origin.
- API Gateway stages are named `dev` and have no throttling configured; set per-route throttling (e.g. 10 rps burst per user is plenty) so a single account cannot run up Bedrock spend.

### IAM least privilege (from the stack files)

| Role | `resources: ['*']` grants | Recommendation |
|---|---|---|
| `ApiHandler` (`sessions-stack.ts`) | `events:PutEvents` | Scope to the default event bus ARN. |
| `DefaultHandler` and `AgenticRetrievalFunction` | `execute-api:ManageConnections` | Scope to `arn:aws:execute-api:<region>:<acct>:<wsApiId>/*/POST/@connections/*`. |
| `AgenticRetrievalFunction` (`graphrag-messages-stack.ts`) | `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`; `bedrock:Retrieve` on `knowledge-base/*` | List the 4 model / inference-profile ARNs; scope Retrieve to the FAQ KB id. |
| Ingestion task role (`ingestion-stack.ts`) | `bedrock:InvokeModel`; `textract:*` six actions | Textract has no resource-level ARNs (keep), Bedrock: list models. |
| Ingestion task role | R/W on `textract-chunk-result-dhgoel` — a bucket in **another account** | **[F5]** Remove; create a `textract-staging/` prefix in the work bucket and point `TEXTRACT_STAGING_BUCKET` at it. In DOR's account this grant is dead weight at best and a cross-account data path at worst. |
| `ApiHandler` | `s3:PutObject raw/*`, `ecs:RunTask`, `iam:PassRole` (task + execution role) | Only needed for `/admin/ingest`; goes away if that route is removed. |
| Next.js server Lambda (`webapp-stack.ts`) | `s3:GetObject` on `work/visualizer/*` | Fine. |

Neptune and DynamoDB grants are already scoped to the specific graph/table ARNs.

### Network

- **Neptune Analytics `publicConnectivity: true`** (`graphrag-stack.ts`, comment: "so Lambdas can reach it via IAM auth without VPC configuration"). The endpoint is on the public internet but every request must be SigV4-signed by a principal with `neptune-graph:ReadDataViaQuery` on that graph ARN; there is no password path. *Trade-off:* keeps all Lambdas VPC-less (no ENI cold-start penalty, no NAT cost — the ingestion VPC deliberately has `natGateways: 0`). *Alternative if the ISO requires private data-plane:* set `publicConnectivity: false`, attach the retrieval Lambda and the Fargate task to a VPC with a Neptune Analytics interface endpoint, and add NAT (≈ $35/mo/AZ + data) or VPC endpoints for Bedrock, S3, DynamoDB, API Gateway management API. Budget 1–2 days.
- Fargate ingestion tasks run in **public subnets with a public IP** (`run_fargate.sh` and `chat_api` both pass `assignPublicIp: ENABLED`); security group allows all outbound and nothing inbound. Acceptable for a batch job; note it in the threat model.
- No WAF on CloudFront or the HTTP API. Add `AWS::WAFv2::WebACL` with the AWS managed core rule set + rate-based rule on CloudFront (~$10/mo); the `Nextjs` construct accepts a `webAclId` override — verify in the `cdk-nextjs-standalone` version pinned in `infra/package.json`.

### Application / LLM

- **Prompt injection posture.** The tool loop (`backend/lambdas/agentic_retrieval/loop/phase_a.py`) exposes only read-only retrieval tools (`vector_search`, `search_document`, `get_section`, `get_neighbors`, `faq_search`, `list/get_worksheet`, `get_flowchart`, `fetch_case_opinion`, `prepare_answer`); there is no tool that writes, sends, or fetches arbitrary URLs, so the blast radius of an injected instruction inside a corpus chunk is limited to a wrong/omitted answer. Corpus content is DOR-published PDFs, not user uploads — except via `/admin/ingest` (see F4). The Haiku disambiguation/topic-shift pre-check is a UX classifier, not a safety filter; Bedrock Guardrails is the drop-in if the ISO asks for one (add `guardrailConfig` to the converse calls, ~0.5 day).
- User content in CloudWatch: `config/retrieval.toml` defaults `LOG_QUERY_TEXT`, `LOG_NEPTUNE_QUERY_TEXT`, `LOG_TOOL_TRACE`, `LOG_AGENT_TRACE` all `true` (500/1000-char previews), and Lambda log groups have **731-day retention**. Also `connect.py` logs the full connect event, `chat_api` logs message bodies on error. For production: set the two `*_QUERY_TEXT` flags `false` in `graphrag-messages-stack.ts` overrides, set `logRetention` on every function to 30–90 days, and add `--filter` in CloudWatch for `email`.
- **Data retention / PII.** `ChatHistoryTable` holds every query and answer indefinitely; `SessionTable` stores the user's email on each session; DynamoDB encryption is AWS-managed (chat history) / default (sessions). Users will type parcel numbers, owner names, and case specifics. DOR needs a written retention policy; implementation is a DynamoDB TTL attribute (the sessions table already writes `ttl` on connect only) plus an export-then-purge for the admin activity feature. Public-records implications are DOR's call — flag it to Amy.
- Secrets: repo grep for `AKIA…`, `api_key=`, `apiKey:`, `password:` and 12-digit account ids returns **nothing** in source (matches outside `node_modules`/`cdk.out` only). Runtime config is env vars from CDK; the frontend embeds only the public pool/client ids and API URLs. There is no Secrets Manager usage today and none is needed until an IdP client secret arrives (OIDC) — store that in Secrets Manager and reference it from CDK.
- Dependency scan. `cd frontend && bun audit` on 2026-09-14: **171 advisories (8 critical, 90 high, 59 moderate, 14 low)**, predominantly transitive (picomatch ReDoS etc.) under `next@15.4.5` / `cdk-nextjs-standalone` toolchains; run `bun update`, re-audit, and bump Next.js to the current 15.x patch before handoff. Python: `uvx pip-audit -r backend/lambdas/agentic_retrieval/requirements.txt` (repeat for `chat_api`, `websocket`, both layers, and `tools/ingestion/docker/requirements.txt`). Add both commands to the (currently non-existent) CI, §5.
- Infra hygiene: `deletionProtection: false` on the graph and `RemovalPolicy.DESTROY` on the user pool, chat history, model-config and FAQ-URL tables — flip to RETAIN / protection on before DOR runs `cdk deploy` in anger. Enable point-in-time recovery on the two DynamoDB tables that hold user data.

### Suggested tests for the specialist

ZAP/Burp against CloudFront + HTTP API with a low-priv token (F1–F3), `prowler` or Security Hub CIS benchmark on the new account, IAM Access Analyzer on the roles above, a prompt-injection corpus run through `run_graph_regression.py`, and a Neptune reachability check from an unauthenticated client (expect 403).

---

## 4. Cost model

Cost Explorer was not readable from the DxHub profile used for this inspection (only S3 appeared, $9 for Sept 1–13), so the numbers below are list prices × measured usage. Ask DOR's account manager for the real August invoice as a cross-check.

**Measured usage, last 30 days (CloudWatch Logs Insights on the retrieval Lambda):** 209 queries, avg 3.9 agentic turns, avg 25 s Phase A + ~11 s Phase B. Sonnet 4.6 tokens across those 209 queries: Phase A — 22.2 M cache-read, 7.9 M cache-write, 0.2 M output, ~0 uncached input (prompt caching is working); Phase B — 2.4 M cache-write, 0.2 M cache-read, 0.18 M output. Per query ≈ 106k cache-read + 38k cache-write + 1k output (A) plus 12k cache-write + 1k output (B).

### Per-query variable cost (Bedrock, Sonnet 4.6 at $3 / $15 per MTok; cache write 1.25×, read 0.1×)

| Component | Tokens / query | $ / query |
|---|---|---|
| Phase A cache read | 106k × $0.30/M | 0.032 |
| Phase A cache write | 38k × $3.75/M | 0.141 |
| Phase A + B output | 1.9k × $15/M | 0.028 |
| Phase B cache write | 12k × $3.75/M | 0.046 |
| Haiku 4.5 disambiguation / topic-shift (~3k in, 0.1k out) | | 0.003 |
| Sonnet auto-refine rewrite, Titan query embeddings | | ~0.01 |
| **Total** | | **≈ $0.26** (use $0.25–0.35; long follow-up threads push cache-write up) |

The LLM judge (`run_graph_regression.py`) and staging graphs are dev-only and not in the run rate.

### Fixed monthly

| Driver | Basis | $ / month |
|---|---|---|
| **Neptune Analytics, 32 m-NCU** (floor; 16 rejected) | $3.20/hr × 730 h | **~$2,340** |
| **OpenSearch Serverless for the FAQ KB** | standby replicas ENABLED → 4 OCU minimum × $0.24/OCU-hr × 730 h (2 OCU ≈ $350 if standby is disabled) | **~$700** |
| Lambda, API Gateway, EventBridge, DynamoDB on-demand, CloudFront, X-Ray | pilot volume | ~$20–40 |
| S3 (2.5 GB + requests, incl. Next.js assets) | Sept run-rate | ~$20 |
| CloudWatch logs (15 MB stored, INFO traces on) | | <$10 at pilot; scale with volume unless retention is cut |
| WAF (if added) | 1 ACL + managed rules | ~$10 |

### Refresh (per annual/major corpus refresh, not monthly)

Neptune at 128 m-NCU for ~1 h ≈ $13; Nova 2 Lite alias generation ≈ $10; Fargate 2 vCPU/8 GB for a few hours ≈ $2; Titan re-embed of ~10k chunks ≈ $0.10; Sonnet doc classification cached unless `--reclassify` (then ~$20–40); Textract fallback pages a few dollars. **≈ $50–100 per full refresh**, negligible amortized.

### Monthly scenarios (117 users)

| Scenario | Queries / month | Bedrock | Fixed | **Total** |
|---|---|---|---|---|
| Low — pilot rate (259 q / 25 d ≈ 310/mo), aoss standby off | 310 | $80 | $2,340 + $350 + $60 | **≈ $2,850** |
| Expected — 5× pilot | 1,550 | $400 | $2,340 + $700 + $80 | **≈ $3,500** |
| High — 20× pilot | 6,200 | $1,700 (at $0.27) | $2,340 + $700 + $120 | **≈ $4,900** |

Sanity check on "high": 6,200 queries/month over 117 people is ~2.5 queries per person per working day — plausible for a reference tool, so budget for it.

**Levers if DOR wants the number down:** (1) the FAQ KB's OpenSearch Serverless is ~$700 for 1,244 small FAQ files — the FAQs are already in the Neptune graph at authority level 6, so `faq_search` could be re-pointed at a Neptune vector query and the KB + collection deleted (1–2 days; saves $350–700/mo — the largest optional saving); (2) confirm with the Neptune team whether the graph can be compacted enough to fit 16 m-NCU (halves the dominant line; last attempt was rejected on storage memory); (3) trim Phase A cache-write by shortening tool results — the 38k cache-write per query is the largest LLM line.

---

## 5. Repo transfer

- **Today:** public GitHub repo `cal-poly-dxhub/wisconsin-dor-polished`, 546 commits on `main`, no CI (`.github/workflows` does not exist), no secrets in source (grep above), account-specific values limited to bucket names/table names/log group names in CLAUDE.md and memory notes (public already).
- **Target:** DOR owns a copy under their GitHub org (or Azure DevOps if that is their standard — ask Brad). Options, cheapest first:
  1. **Fork/mirror as-is** — full history, DxHub keeps upstream for coaching. Zero effort; requires the Task 63 decision below to be "leave".
  2. **Transfer a squashed copy** — `git checkout --orphan` from `main` HEAD, single "Initial import from Cal Poly DxHub" commit, push to DOR's repo. DxHub keeps the historical repo private. 30 minutes; loses `git blame`, keeps everything else. **Recommended.**
  3. **`git filter-repo` purge + force-push** of the public repo — rewrites 13 commits touching the two test YAMLs (`tools/ingestion/tests/graph_regression_queries.yaml`, `classifier_regression_queries.yaml`) plus the old `docs/tasks.md` revisions. Invalidates every clone/PR ref; 2–3 hours incl. verification; only worth it if the public repo must itself be clean.
- **Task 63 decision, restated:** the residual exposure is 30 production `queryId`s and tester-complaint framing in history (HEAD is clean since PR #32). Those queryIds are opaque UUIDs that resolve only against a DynamoDB table that will not be migrated; the framing is embarrassing, not sensitive. Recommendation: option 2 for the DOR copy (their engineers never see the history), and **make the public repo private** at handoff rather than rewriting it — that closes both the Task 63 concern and the "DOR eyes on history" concern with no force-push. Get Valeah's written OK on the residual exposure either way.
- **CI DOR should expect (add before transfer, ~half a day):** a GitHub Actions workflow running `bun install`, `bun run test` (frontend bun tests + `infra/test/infra.test.ts` Jest synth assertions), `uv run pytest`, `bunx eslint .`, `uv run ruff check .`, `bun audit --audit-level=high`, `pip-audit` per requirements file. Deploy stays manual (`bun run bundle && cdk deploy`) with a `cdk diff` gate, per CLAUDE.md.
- Hand over with the repo: `CLAUDE.md`, `docs/annual-refresh-runbook.md`, `docs/testing.md`, `docs/graphrag-engineering-guide.md`, and this plan; strip DxHub-specific paths from CLAUDE.md (the `~/Work/DxHub/wisdor` references, hardcoded resource names → "read from stack outputs").

---

## 6. Sequence, owners, effort

Dependencies in bold. Hours are engineering effort, not elapsed time.

| # | Step | Owner | Hours | Depends on |
|---|---|---|---|---|
| 1 | Send DOR IT the SSO questionnaire (§2) and the cost sheet (§4); request the AWS account, an admin deploy role, and account-manager engagement (Bedrock model access + quotas) | Darren | 2 | — |
| 2 | Fix F2, F3, F5, `selfSignUpEnabled: false`, CORS origin, log flags/retention, RETAIN/deletion-protection flips, put `Admins` group in CDK | Isaac | 12 | — (can start now) |
| 3 | `bun update` + re-audit, `pip-audit` pass, add CI workflow | Isaac | 6 | — |
| 4 | Decide F4 (keep-gated vs. remove `/admin/*`) with Valeah/Brad; implement | Isaac (decision: DOR) | 4–8 | DOR decision |
| 5 | Security assessment on the *current* DxHub stack using §3 as scope; written findings for the ISO | Darren's specialist | 16–24 | steps 2–4 merged |
| 6 | **Cost sign-off** from Valeah/Amy (incl. FAQ-KB lever decision) | DOR | — | step 1 |
| 7 | SSO: CDK IdP + hosted-UI client + frontend redirect flow; test with a DOR test identity | Isaac + DOR IdM | 16 | **SSO answers from step 1**; DOR app registration |
| 8 | Repo: squash-copy to DOR org, CLAUDE.md cleanup, private-ize public repo (Task 63 close) | Isaac; Valeah OK | 3 | step 3 |
| 9 | Account build: `cdk bootstrap`, `cdk deploy` (CloudWatch IAM flag on), Bedrock model access, `s3 sync` ×3, seed tables, KB ingest, ECR push, Neptune load at 128 → 32, regression smoke test | Isaac (Darren pair) | 10 + ~3 h waiting | **step 6**, DOR account + deploy role |
| 10 | Re-run the assessment's automated checks against the DOR account; ISO review with Brad/Amy | specialist + DOR | 6 | steps 5, 9 |
| 11 | Pilot testers re-login via SSO in DOR account; 1-week parallel run; cut over, then `cdk destroy` DxHub stack (keep buckets: RETAIN) | Isaac / Valeah | 4 | steps 7, 9, 10 |
| 12 | Runbook walkthrough with DOR's data-maintenance owner (annual refresh, prompt upload, log triage) | Isaac → DOR engineer | 4 | step 11 |

**Total engineering ≈ 85–100 hours** (Isaac ~60, specialist ~25, Darren ~10) plus DOR IdM/ISO time. Critical path is **step 1 → 7** (SSO details) and **step 6 → 9** (cost sign-off before the account move). If the SSO answers arrive by Sep 19 and the account by Sep 24, steps 2–5 land during the final testing week, the account build happens the week of Sep 28, and the ISO review + cutover fit the first two weeks of October. If SSO slips, everything else can still move on schedule with the interim `selfSignUpEnabled: false` + pre-created Cognito users, and SSO is swapped in later without touching the API or data.
