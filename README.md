# Lyftr

A chat application with ordered API-key failover, LiteLLM response caching, and persistent SQLite conversations. Built with React, TypeScript, Vite, and FastAPI.

## Live URLs

The frontend is deployed on Vercel and the backend runs on EC2 with HTTPS. The public frontend is working, and stored history survived backend recreation. The narrated demo is pending.

- Frontend: https://lyftr-zeta.vercel.app
- Backend: https://lyftr.3.25.69.252.sslip.io — verified from a public client for HTTPS health, real inference, cache, failover, memory, and failed-turn integrity.
- Public repository: https://github.com/Webrookie0/lyftr — full source published; GitHub Actions passed.
- Narrated demo: pending recording and public sharing.

Local frontend: `http://localhost:3000`. Local backend: `http://localhost:8000`.

## Run locally: with Docker and without

You need two distinct usable keys for the same model. The suggested model is `gemini/gemini-2.5-flash-lite`; confirm access in your provider account. Keys in one provider project may share the same quota, so separate keys do not guarantee independent capacity. Keep inference within your free-tier allowance.

With Docker Desktop running:

```bash
cp .env.example .env
# Edit .env and replace both LLM_API_KEYS placeholders with real keys.
make up
curl http://localhost:8000/health
```

Open `http://localhost:3000`. Use `make logs` for startup logs and `make down` to stop. Ordinary shutdown preserves the `sqlite_data` volume. The backend requires at least two distinct keys at startup. A healthy key pool reports configuration; a real chat verifies provider availability.

Without Docker, install Python 3.12+, uv, and Node 22.12+ or 24. From the repository root:

```bash
cp .env.example .env
# Edit the keys. Change DATABASE_URL to sqlite:///./data/app.db for a direct run.
cd backend
uv sync --python 3.12
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

For a different backend, set `VITE_API_BASE_URL` before starting or building the frontend. For example, `VITE_API_BASE_URL=https://your-api.example npm run build`. The backend reads the root `.env`; Vite uses its build environment or a `frontend/.env.local` file. Frontend configuration is public and must never contain API keys.

Run verification from the root:

```bash
make test
make test-frontend
make lint
make build
make verify
```

`make test` requires LiteLLM and runs the complete backend suite. Individual API/storage tests can also run with `cd backend && python -m unittest discover -s tests -v`; the native integration class explicitly skips if LiteLLM is missing. These tests use controlled inference or mock provider HTTP transport. `make test-frontend` runs six real Markdown/code-block rendering checks for highlighting, language labels, inline-code behavior, unsupported-language fallback, and HTML escaping. `make verify` calls the actual running backend and real providers, consuming their quota.

Verification on 21 November 2026: all 33 backend tests and six frontend rendering tests passed. Ruff lint/formatting, TypeScript compilation, and the Vite production build passed. GitHub Actions passed for the published source. Local, Oracle, and VM Compose configurations validated. Dependencies are locked in `backend/uv.lock` and `frontend/package-lock.json`; both Dockerfiles use the lockfiles. Real-provider checks passed against both the local Docker backend and the public EC2 HTTPS API: Gemini inference, immediate native cache hits with identical usage and lower latency, real key_2 answers after simulated 429/500, warmed-cache wildcard 503 with unchanged history, and multi-turn memory. Local browser chat, reply metadata, the black theme, anchored composer, and syntax highlighting were confirmed, as was local history persistence after a backend restart. The deployed frontend was confirmed working after the production API URL was configured. Public HTTPS health returned HTTP 200 with the exact Vercel allow-origin header, two configured keys, and caching enabled. Saved user/assistant messages and their metadata were retrieved through the public API after the EC2 backend container was recreated. Mobile visual QA remains pending.

### AWS EC2 backend and Vercel frontend

The backend currently runs on an Ubuntu 24.04 `t3.small` instance in AWS `ap-southeast-2`, with Docker Compose, Caddy HTTPS, and persistent SQLite storage. The frontend is deployed separately on Vercel. See [the EC2 deployment guide](deploy/AWS.md) for account eligibility, instance/network settings, Docker setup, HTTPS, and verification.

The host-independent deployment command is:

```bash
docker compose -f docker-compose.yml -f deploy/compose.vm.yml up --build -d backend caddy
```

For an eligible new customer, AWS's current Free account plan starts with $100 in credits and opportunities to earn up to $100 more. It ends after six months or credit exhaustion. Remain on the verified Free account plan for the ₹0 budget; EC2, disk, public IPv4, and traffic consume credits. This is time-limited hosting rather than an indefinite free VM. Check the [official AWS terms](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html) and actual Billing page before provisioning.

### Oracle backend and Vercel frontend

Use only an Always Free-eligible Oracle VM and volume. The selected budget is ₹0. Verify eligibility and regional capacity in the account before provisioning. On the VM, install Docker Engine and Compose v2.24.4+ (the deployment override uses `!reset`). Copy this repository and a private `.env` to the VM.

1. Point a DNS hostname at the VM's public IP. Allow TCP ports 80 and 443 through Oracle's network rules and the VM firewall; retain the SSH access needed to manage the VM. Caddy obtains an HTTPS certificate for that hostname.
2. Set `BACKEND_HOST`, `ACME_EMAIL`, `DATABASE_URL=sqlite:////data/app.db`, `PORT=8000`, both API keys, and `ENABLE_FAILURE_SIMULATION=true`. Set `ALLOWED_ORIGINS` to the exact intended Vercel frontend origin.
3. Start the backend and HTTPS proxy:

```bash
docker compose -f docker-compose.yml -f deploy/compose.oracle.yml up --build -d backend caddy
curl https://your-backend-hostname/health
```

The Oracle override removes the backend's host port. Caddy reaches it on Docker's internal network. The local frontend service is excluded unless the `local-ui` profile is explicitly enabled. SQLite and certificate data use persistent named volumes.

4. Import the public repository in Vercel on the Hobby plan, set the project root to `frontend`, and configure `VITE_API_BASE_URL=https://your-backend-hostname`. Build with `npm run build` and serve `dist`. Update the backend's exact CORS origin if Vercel assigns a different URL, then recreate its container. Changing the frontend API URL requires a rebuild.
5. Run `LYFTR_API_URL=https://your-backend-hostname python3 scripts/verify_live.py`. Verify the frontend in a browser too. Create a conversation, restart only the backend (`docker compose -f docker-compose.yml -f deploy/compose.oracle.yml restart backend`), and retrieve its history to prove persistence.

Protect the `.env` file on the VM (`chmod 600 .env`). Keep the backend at one instance and one worker. Provider key secrets stay in backend configuration and are never returned by the API.

## Design decisions: key fallback, caching, SQLite session storage

The application exposes `GET /health`, `POST /chat`, and `GET /sessions/{session_id}/messages` directly under the backend URL. It disables the OpenAPI and documentation routes. HTTP errors return JSON with a `detail` field.

Keys are labeled `key_1`, `key_2`, and so on in configuration order. An explicit asynchronous loop calls LiteLLM with one key at a time. Rate limits, quota errors, provider failures, invalid replies, and timeouts advance to the next key. SDK retries are zero and each entire attempt has a bounded timeout. Exhaustion returns HTTP 503 with `{"detail":"all providers unavailable"}`. Attempt counts are known before returning the response; routing does not depend on asynchronous logging callbacks or retained cooldown state.

This replaces the initial proposed Router hook integration: the documented per-deployment hooks were absent from the inspected older release. The explicit key loop uses the LiteLLM SDK for actual provider calls and normalized errors, while keeping simulation and fallback accounting local to each request.

Caching uses LiteLLM's actual `Cache(type="local")` with awaited native lookup/insertion. Its key is a SHA-256 digest of the configured provider/model name and the complete ordered provider message list, including the effective system prompt. Sessions and API keys are excluded. Generation settings are fixed. A reply originally produced by key_2 can therefore satisfy an equivalent prompt for another session. Cache hits retain reply text and the original prompt/completion usage; they return a null key ID, zero failed attempts, and fresh server-measured latency. In-memory cache entries expire after `CACHE_TTL_SECONDS` and reset on process restart.

Enabled `X-Simulate-Failure` requests bypass cache reads and writes. This makes failure demos reliable even after the same prompt has warmed the cache. The header is parsed only when `ENABLE_FAILURE_SIMULATION=true`. It supports 429, 500, 502, and 503 with specific key IDs or `*`; malformed enabled headers return 422. Simulation does not affect other requests.

SQLite retains the required `messages` schema, an ordering index, and a small `sessions` table for the system prompt. Startup creates the schema automatically. WAL and a busy timeout support concurrent sessions. Per-session locks serialize turns for one conversation. Successful user/assistant pairs and prompt updates commit in one short transaction after inference; failed requests add nothing. Legacy assistant rows with partial metadata remain readable. The named `/data` volume preserves history across container restarts.

The client generates a new session on page load or New conversation, sends only the new message, and renders Markdown with raw HTML disabled. The app frame is constrained to the viewport: the header and composer/footer stay anchored while only the conversation pane scrolls. Auto-scrolling targets that pane and pauses when the user reads older messages. Fenced code appears in dedicated blocks with language labels, Copy code, and muted syntax highlighting; long code lines scroll horizontally inside the block. The default system prompt asks for 1–3 sentences on simple factual questions and longer answers when requested or necessary. This reduces future conversation growth while retaining full history. Existing sessions keep their stored instructions; start a new conversation after updating the backend to use the new default. Each reply displays the exact source/model, integer latency, input/output usage, selected key, and fallback count. Network interruptions trigger history reconciliation before another send is permitted. History reads acquire the same session lock as chat, so reconciliation waits for any running turn to finish.

## Verification: how to see failover, cache hits, and per-message token usage

Cache identity includes the full conversation, not just the latest question. Repeating a question after other turns normally reports `LIVE_INFERENCE` because its history changed. The growing input-token count reflects that extra context. To see `LLM_CACHE`, send the identical first question in two fresh conversations with the same system prompt. The cache is in memory and clears when the backend restarts.

Run `make verify-cache` for a focused live check using the first-president question. It verifies a live first response, a cache hit in another fresh session with identical reply/usage, and a live response when repeating the question with newly added history. Each run uses a fresh shared system prompt so previous test runs cannot warm those contexts. The focused live check passed: 1186 ms for live inference, 1 ms for the fresh-session cache hit, and live inference after conversation history changed. The backend regression suite also reproduces the four-turn president/memory/repeated-question sequence and proves a fresh session hits the original first-turn cache.

To see a cache hit in the UI, copy the exact first prompt, start a new conversation, and send it with the same tone. Demo controls were removed from the frontend at the user’s request. Backend failure simulation remains available through the documented HTTP header and verification scripts. After updating frontend code, rebuild only that service with `docker compose up --build --no-deps -d frontend` to keep the backend cache warm.

Set `API` to the running backend and use a new first prompt for the first live request. Repeating it immediately in a fresh session should hit the cache:

```bash
API=http://localhost:8000
curl -sS "$API/health"
curl -sS "$API/chat" -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-one","message":"What is the capital of France?"}'
curl -sS "$API/chat" -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-cache","message":"What is the capital of France?"}'
```

The first response reports `LIVE_INFERENCE`; the second reports `LLM_CACHE`, identical reply and usage, `key_id: null`, and `fallback_attempts: 0`. The latency is independently measured for each request. Later turns include conversation history in their input usage, so input counts may increase.

Force first-key failover and then complete failure, including on the already-cached prompt:

```bash
curl -sS "$API/chat" -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"demo-failover","message":"What is the capital of France?"}'
curl -sS "$API/chat" -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=500' \
  -d '{"session_id":"demo-server-error","message":"What is the capital of France?"}'
curl -sS "$API/sessions/demo-one/messages"
curl -i "$API/chat" -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"demo-one","message":"What is the capital of France?"}'
curl -sS "$API/sessions/demo-one/messages"
```

The first two should produce a real reply through key_2 with one fallback. The wildcard returns the exact required 503 body; the history before and after remains identical. Also test `key_1=429,key_2=500` with two configured keys. With more keys, the remaining keys are still eligible.

Use `scripts/verify_live.py` to automate those checks, real memory, cache token preservation, and lower server latency. For multi-turn memory in the UI, use the “Put memory to the test” suggestion and ask which city you mentioned. Inspect per-reply metadata and the public history endpoint. Reloading or starting a new conversation creates a separate session.

For the narrated 2–3 minute demo: show the public app and `/health`; send a message and a memory follow-up; repeat the first prompt in a new conversation to show cache and latency; demonstrate first-key 429 failover; show wildcard 503 and unchanged history; finish on the public repository and history endpoint. Verify all four submission URLs open without login before submitting.

## Setup Used: your editor and AI tools

Implementation continued in the Codex workspace on macOS using the Codex coding agent, terminal tools, Python, uv, Node, npm, and Docker Compose. AI-assisted implementation was checked with 33 API/storage and native LiteLLM integration tests, six frontend rendering tests, Ruff, TypeScript compilation, a production bundle, and Compose configuration validation. Real Gemini, local Docker, browser chat/metadata, local container-restart persistence, and the public EC2 API were verified with terminal output and browser feedback. GitHub Actions passed, the deployed frontend was confirmed working, and public session history survived EC2 backend recreation.

## Bonus features (if any): what you added beyond the requirements and why

- Session instructions let a user choose a response tone and persist it alongside successful turns.
- Copy reply supports moving useful answers into another document.
- Repeatable terminal verification scripts demonstrate caching and failure recovery while the frontend focuses on conversation.
- Backend health shows loaded keys and cache status.
- Failed requests restore the composer; interrupted connections reconcile stored history before retrying.
- A matte-black and graphite interface with responsive navigation, visible reply metadata, keyboard sending, reduced-motion support, and Markdown replies makes the conversation usable across screen sizes.
