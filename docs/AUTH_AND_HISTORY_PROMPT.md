# Build Brief — Accounts, Privacy, and Conversation History

Paste this whole file as a single prompt into Claude Code, opened at the repo root
(`speach-to-speach ai/`). It is a specification, not a suggestion: §8 and §14 are security
requirements, and the acceptance checklist in §17 is what "done" means.

---

## 0. Role

You are adding **identity and persistent conversation history** to Speech Confidence Coach.

Today the app is single-tenant and anonymous: anyone who opens it shares one pool of
practice sessions. After this work, a person signs in, sees only their own conversations,
can resume any of them and keep talking inside that same thread, or start a fresh one — and
nobody else, including an administrator, can read what they said.

The transcripts here are a record of someone's speech difficulties. Treat them as the most
sensitive data in the system, because they are.

---

## 1. Ground truth — read before designing anything

Do not invent a schema. Most of the conversation model already exists and is good.

| File | What's already there |
|---|---|
| `backend/app/db/models.py` | `Session` (id, started_at, ended_at, **title**) and `Turn` (role, mode, text, acoustic, citations, timings). Audio is deliberately never stored. |
| `backend/app/api/routes/sessions.py` | Full CRUD + `/progress` + `/{id}/metrics` — all of it currently **unowned** |
| `backend/app/services/orchestrator.py` | `history_for()` already feeds prior turns into the prompt, and auto-titles a session from its first user turn (`templates.build_session_title`) |
| `backend/app/db/session.py` | Async SQLAlchemy, SQLite, `create_all()` at boot — **no Alembic migrations exist yet** despite alembic being in requirements |
| `frontend/src/lib/api.ts` | `createSession`, `listSessions`, `getSession`, `deleteSession`, `endSession` — **already written, and the UI never calls three of them** |
| `frontend/src/hooks/useCoachSession.ts` | Owns one session at a time; `sessionId` state is the thread pointer you will be switching |
| `frontend/src/App.tsx` | Single page, **no router** |
| `docs/ETHICS.md` | Outranks everything in this document |
| `design/UI_REBUILD_PROMPT.md` | The visual system every new screen must match |

So the work is not "build a chat app". It is: **add a `User`, hang everything off it, enforce
that boundary everywhere, and finally build the sidebar the API has been waiting for.**

---

## 2. Skills

- **`design`** — artboards for the new screens before you code them: sign in, create account,
  empty state (first ever login), the sidebar with a populated history, resumed conversation,
  account & privacy settings, delete-account confirmation.
- **`artifact-design`** — type, spacing and hierarchy discipline on the auth screens. A login
  page is where "vibe-coded" shows most, because there is almost nothing on it to hide behind.
- **`humanizer`** — every error message, every empty state, every consent string. Error copy
  is where AI register is most obvious and most damaging: "Oops! Something went wrong 😅" is
  a failure, not a fallback.
- **`dataviz`** — only if you surface per-conversation stats in the sidebar.

---

## 3. Locked decisions

These were decided already. Do not re-litigate them; implement them.

1. **JWT access token + rotating refresh token.** Not cookie sessions.
2. **Strictly single-owner.** No admin view, no clinician view, no support back door. If you
   find yourself writing a query that returns another account's turns, you have made a
   mistake.
3. **Local demo, built like production.** SQLite is fine. Argon2, rate limiting, lockout,
   token rotation, reuse detection and the full test suite are *not* fine to skip — they are
   the point. Everything must be demonstrable on localhost.
4. **Non-clinical language throughout.** The UI says "you", "your account", "your practice".
   The code says `user`. Never `patient` — this project's own Readme and `ETHICS.md` state it
   is not a medical device and does not diagnose, and clinical vocabulary quietly contradicts
   that on every screen.

**A note you must respect while implementing #1:** a JWT is a bearer credential the server
cannot un-issue. That is a real weakness for this data, and the design below compensates for
it deliberately — short access lifetime, memory-only storage, and all the revocation power
moved onto the refresh side. Do not "simplify" any of those away; without them this choice is
strictly worse than a session cookie, and your report should say so honestly.

---

## 4. Data model

Add to `backend/app/db/models.py`:

```python
class User(Base):
    __tablename__ = "users"
    id: str                    # uuid4 hex[:16], same convention as Session
    email: str                 # citext-equivalent: store lowercased, UNIQUE index
    password_hash: str         # argon2id
    display_name: str | None   # optional, what the UI greets them by
    created_at: datetime
    last_login_at: datetime | None
    email_verified: bool = False      # designed for, not enforced in the demo
    failed_attempts: int = 0
    locked_until: datetime | None
    memory_enabled: bool = True       # see §12

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: str
    user_id: FK(users.id, ondelete="CASCADE")
    token_hash: str            # sha256 of the opaque token. NEVER store the token itself.
    family_id: str             # shared by every token descended from one login
    issued_at: datetime
    expires_at: datetime
    used_at: datetime | None   # set on rotation; a second use is a reuse attack
    revoked_at: datetime | None
    user_agent: str | None     # for the "your sessions" list
    __table_args__ = (Index("ix_refresh_user", "user_id"), UniqueConstraint("token_hash"))

class WsTicket(Base):
    __tablename__ = "ws_tickets"
    id: str
    user_id: FK(users.id, ondelete="CASCADE")
    token_hash: str
    expires_at: datetime       # issued_at + 30s
    used_at: datetime | None   # single use

class UserMemory(Base):        # see §12 before building this
    __tablename__ = "user_memory"
    id: str
    user_id: FK(users.id, ondelete="CASCADE")
    content: str               # one short durable fact, in the user's own words
    source_session_id: str | None
    created_at: datetime
```

Modify `Session`:

```python
user_id: Mapped[str] = mapped_column(
    ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
)
```

`Turn` needs no change — it reaches the owner through its session, and denormalising the
owner onto it creates two places for the answer to disagree.

**Migration.** There are no migrations yet and `create_all()` cannot add a NOT NULL column to
a populated table. So: `alembic init`, wire `env.py` to `settings.database_url` and
`Base.metadata`, generate the baseline as the current schema, then a second revision that adds
users, tokens, tickets, memory, and `sessions.user_id`. For existing rows, either drop the dev
database (state this in the migration's docstring) or backfill them to a single migrated
account — pick one and say which. Keep `create_all()` working for the test suite.

---

## 5. Authentication design

**Password hashing** — `argon2-cffi`, argon2id, OWASP parameters:
`time_cost=3, memory_cost=65536, parallelism=4`. Add `argon2-cffi==23.1.0` to
`requirements.txt`. Never bcrypt-with-a-72-byte-truncation-surprise, never SHA-anything.

**Password policy** — NIST 800-63B, not the 2005 rules: minimum 12 characters, maximum 128,
no composition requirements, no forced rotation, and a check against a small bundled list of
the most common passwords. Reject on length and commonness only.

**Access token** — JWT, HS256.
- Claims: `sub` (user id), `jti`, `iat`, `exp`, `typ: "access"`.
- Lifetime **10 minutes**.
- Secret from `SCC_JWT_SECRET`, minimum 32 bytes. **The app must refuse to boot** if it is
  unset or shorter while `debug=False`. A default secret in a config file is a vulnerability,
  not a convenience.
- Verify `typ` on every decode. A refresh token must never be accepted as an access token.

**Refresh token** — deliberately *not* a JWT.
- 32 cryptographically random bytes, base64url. The database stores only its SHA-256.
- Lifetime 14 days, sliding via rotation.
- Delivered as a cookie: `HttpOnly; Secure; SameSite=Strict; Path=/api/auth`.
  (`Secure` may be conditioned on `settings.debug` for localhost http — condition it on the
  setting, never remove it.)
- **Rotation:** every call to `/api/auth/refresh` invalidates the presented token and issues a
  new one in the same family.
- **Reuse detection:** if a token that already has `used_at` is presented, revoke *the entire
  family* immediately and return 401. That is the standard signal of a stolen token, and
  handling it is the single thing that makes rotating refresh tokens worth the complexity.

**Rate limiting and lockout**
- Login: 5 attempts per 15 minutes, keyed on `(email, client IP)`.
- Register: 3 per hour per IP.
- Refresh: 30 per hour per user.
- After 5 consecutive failures set `locked_until` with exponential backoff capped at 15
  minutes. Never lock permanently — a permanent lock is a denial-of-service anyone can trigger
  against a known email.
- Implement as a small in-process limiter (`slowapi`, or ~40 lines of your own with a
  `dict[str, deque]`). Note in a comment that it is per-process and would move to Redis if
  this were ever horizontally scaled.

**No user enumeration**
- `/register` with an existing email returns the *same* 201-shaped response as a new signup;
  the account is simply not created and no email is sent.
- `/login` returns one message for both wrong-password and no-such-user:
  `"Those details don't match an account."`
- When the email is unknown, still run a dummy argon2 verification against a fixed hash so
  the response time does not reveal which branch ran.

**WebSocket authentication** — the part most implementations get wrong. Browser `WebSocket`
cannot send an `Authorization` header, and putting a JWT in the query string writes a live
credential into every access log and browser history entry. So:

1. Client calls `POST /api/auth/ws-ticket` over normal authenticated HTTP.
2. Server returns a single-use opaque ticket valid for **30 seconds**.
3. Client opens `ws://.../ws/knowledge?ticket=…`.
4. The handler resolves the ticket to a user, marks it used, and **closes with 4401 if it is
   missing, expired, already used, or unknown** — before touching the microphone stream.
5. Every turn the handler writes carries that user's id.

Update `wsUrl()` in `frontend/src/lib/api.ts` to take the ticket, and fetch a fresh one on
every connect. Never cache a ticket.

---

## 6. API surface

New router `backend/app/api/routes/auth.py`, prefix `/api/auth`:

| Method | Path | Body / effect | Returns |
|---|---|---|---|
| POST | `/register` | email, password, display_name? | 201, sets refresh cookie, returns access token + user |
| POST | `/login` | email, password | 200, sets refresh cookie, returns access token + user |
| POST | `/refresh` | *(cookie only)* rotates | 200, new access token, new cookie |
| POST | `/logout` | revokes the presented token | 204, clears cookie |
| POST | `/logout-all` | revokes every family for the user | 204 |
| GET | `/me` | — | current user |
| PATCH | `/me` | display_name, memory_enabled | updated user |
| POST | `/me/password` | current_password, new_password | 204, revokes all other families |
| POST | `/ws-ticket` | — | `{ticket, expires_in}` |
| GET | `/me/export` | — | full JSON: account, sessions, turns, memory |
| DELETE | `/me` | requires current password | 204, hard-deletes everything |

`DELETE /me` means **hard delete**: the account row, every session, every turn, every token,
every memory entry, gone, by cascade, in one transaction. No `is_deleted` flag. Someone who
asks to be erased is not asking to be hidden.

Every existing route in `sessions.py`, `chat.py`, `live.py`, `knowledge.py`, and
`corpus.py` gains the auth dependency. `/api/health` and `/api/status` stay public but must
not leak per-user information.

---

## 7. Ownership enforcement

This is the security core of the whole task. Write it once, centrally, and use it everywhere.

```python
# app/api/deps.py
async def current_user(...) -> User: ...      # decodes access token, 401 on any failure

async def owned_session(
    session_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionRow:
    row = await db.get(SessionRow, session_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("That practice session doesn't exist.")
    return row
```

Rules, all of them non-negotiable:

- **404, never 403,** for a session belonging to someone else. A 403 confirms the id exists,
  which is itself a leak. The identical message for "absent" and "not yours" is correct.
- **Never** trust a `user_id` that arrived in a request body or query string. The only source
  of identity is the verified token.
- Every `select(SessionRow)` and every aggregate — including `/progress`, `/{id}/metrics`, and
  anything you add — carries `.where(SessionRow.user_id == user.id)`. `/progress` computing
  across all rows would silently average strangers' speech into someone's chart.
- `create_session` sets `user_id` from the token, always.
- The WebSocket handlers resolve the user from the ticket and refuse to write a turn into a
  session they do not own.
- Add a test that is *only* about this: create two accounts, have each create a session, then
  assert every single session-scoped endpoint returns 404 across the boundary. If a route is
  added later without ownership, that test should be what fails.

---

## 8. Frontend — auth

Add `react-router-dom@^7`. Routes: `/login`, `/register`, `/` (guarded), `/settings` (guarded).

**Token storage.** The access token lives in a **module-scope variable inside `lib/auth.ts`,
plus React context. Never `localStorage`, never `sessionStorage`.** Anything in web storage is
readable by any script that gets injected into the page; a memory-held token dies with the tab
and is the entire reason this design is defensible. The refresh cookie is `HttpOnly` and JS
never sees it — that is what survives a reload.

**Boot sequence.** On mount, call `/api/auth/refresh` once. Success → hydrate the user and
render the app. Failure → render `/login`. Show a quiet loading state during this, not a flash
of the login screen.

**Refresh on 401.** In `lib/api.ts`'s `request()`: on a 401 with code `token_expired`, call
refresh **once**, then retry the original request **once**. Guard it with a module-level
single-flight promise so ten parallel requests trigger one refresh, not ten. If refresh fails,
clear state and route to `/login`. Never loop.

**Login and register screens** — match `design/UI_REBUILD_PROMPT.md`: the Night Studio
atmosphere, Bricolage Grotesque heading, Instrument Sans fields, amber as the only accent on
the submit button. Specifics:

- One column, generous vertical rhythm, the atmosphere and vignette doing the work. No
  split-screen hero with a stock photo, no floating glass card.
- Inputs: `--ground-3` well, 1px `--rule` border warming to `--amber` on focus, label above
  the field (never placeholder-as-label), `autocomplete="email"` /
  `"current-password"` / `"new-password"`, `type="email"`, `inputmode="email"`.
- Errors appear beneath the field in `--danger`, announced via `role="alert"`. **No shake
  animation** — a shake is a stress cue in an app used by people who are already anxious about
  performance. A calm colour change and a clear sentence do the job.
- Submit shows in-button progress; the button never changes width mid-request.
- A password strength hint that reflects the real policy (length, not "add a symbol!").
- Register: the ethics line is on the screen, not behind a link — one sentence saying that
  practice transcripts are stored, audio never is, and the account can be deleted at any time.

**Optional, if you want a frictionless viva demo:** a "Try it without an account" button that
provisions an ephemeral account, banners the fact that its data is deleted on sign-out, and
offers to convert it to a real account. Implement it or don't, but if you do, make the
deletion actually happen.

---

## 9. Frontend — the sidebar and conversation history

This is the feature the user actually asked for. The API already supports it.

**Layout.** A persistent left rail, ~280 px, collapsible to icons, over the Night Studio
ground. It holds: account chip at the bottom, "New conversation" at the top, the list between.

**The list.**
- `api.listSessions()` on mount, grouped by recency: **Today / Yesterday / Previous 7 days /
  Previous 30 days / Older**. Never a flat undated list — the grouping is what makes a long
  history usable.
- Each row: the auto-generated title (already produced by `build_session_title`), a relative
  timestamp, and a small mode marker for whether the thread was mostly live or grounded.
- The active conversation is marked with an amber left edge, not a filled background.
- Hover reveals an overflow menu: rename, delete.
- **Rename** is inline-editable in place — click the title, type, Enter commits, Escape
  reverts. Needs `PATCH /api/sessions/{id}` with `{title}` (add it; validate ≤ 120 chars).
- **Delete** removes the row immediately and shows a 6-second undo toast; the DELETE fires
  only when the toast expires. Undo beats a confirmation dialog for a reversible action, and
  is far kinder than "Are you sure?" on a list someone is tidying.
- Empty state, first login: one warm sentence and the New conversation button. Not an
  illustration of a robot.
- Search input filters titles client-side; if history grows past ~200, add
  `GET /api/sessions?q=` and filter server-side (owner-scoped, obviously).
- Keyboard: `Ctrl/Cmd+K` focuses search, `Ctrl/Cmd+Shift+O` starts a new conversation, arrow
  keys move through the list, Enter opens.

**Resuming — "keep talking in the same scope".** Clicking a conversation:

1. `api.getSession(id)` → returns every turn with its acoustic profile and citations.
2. Hydrate `useCoachSession`'s `messages` from those turns, mapping `TurnOut` → `Message`
   (preserve `mode`, `acoustic`, `citations` so old dysfluency timelines still render).
3. Set `sessionId` to that id.
4. Any new turn — spoken or typed — appends to that same session, and because
   `orchestrator.history_for()` already loads recent turns for the prompt, **the coach picks up
   with the context of that thread**. That is the behaviour requested; it mostly needs wiring,
   not inventing.
5. Reset transient state cleanly: close any open socket, stop capture and playback, clear
   `pendingCoachRef`. Switching threads mid-utterance must not bleed audio from the old one
   into the new one — write a test for this.
6. `useCoachSession` needs a new `loadSession(id)` and `newSession()`, and its
   `sessionId`-creation effect must stop auto-creating a session on mount now that threads are
   explicit.

**New conversation.** `api.createSession()`, clear messages, focus the composer, add the row
to the top of the list optimistically. Do not create an empty session on every page load —
that fills the sidebar with untitled ghosts. Create it lazily on the first turn, or create it
on the explicit click only.

**Motion**, per the design brief: rows stagger in on first paint (30 ms apart), the active
marker slides between rows with a shared layout animation rather than cutting, the undo toast
enters with a spring and leaves by collapsing height. All of it inside the reduced-motion
contract.

---

## 10. Cross-conversation memory

The "chat memory" part, kept deliberately narrow, because this is the feature most likely to
cause harm if it is built casually.

- `UserMemory` holds a handful of short, durable practice facts in the person's own words —
  "practising for a presentation in March", "finds phone calls hardest", "prefers I don't
  interrupt". Nothing else.
- It is **opt-in** (`user.memory_enabled`, default on but shown at registration), fully
  visible at `/settings`, and every entry individually deletable. There is no hidden memory.
- The orchestrator injects entries into the system prompt only when enabled, capped at ~40
  entries and ~800 characters.
- **Never write to it:** anything that reads as a diagnosis, a severity judgement, a
  progression narrative ("getting worse"), or an inference about health, mood, or capability.
  It stores what the person said about what they want to practise. Nothing derived, nothing
  clinical, nothing predictive.
- Deleting the account deletes it. Exporting the account exports it.

If any part of that feels like more than you want to build, build the sidebar without it. A
missing feature is fine; a memory that quietly accumulates judgements about someone's speech
is not.

---

## 11. What must never happen

Treat this as the security review you will be asked about in the viva.

1. A response containing a turn, session, metric, or memory belonging to another account.
2. A token in `localStorage`, in a URL, in a log line, or in an error message.
3. A password, or anything derived from one, written to the log at any level.
4. A refresh token stored in plaintext in the database.
5. A default or committed `SCC_JWT_SECRET`.
6. A refresh token accepted twice without the family being revoked.
7. A WebSocket accepting audio before the ticket is validated.
8. An error message that reveals whether an email is registered.
9. Audio persisted anywhere — the current model stores transcripts and derived profiles only,
   and that must remain true.
10. `DELETE /api/auth/me` leaving any row behind. Verify by counting rows in the test.

---

## 12. Tests

Existing suites must keep passing: **35 backend, 30 frontend.** Most backend API tests
currently hit unauthenticated routes and will start returning 401 — add an `authed_client`
fixture in `backend/tests/conftest.py` and update them **in the same commit**, so the diff
shows the auth boundary being introduced rather than tests being quietly rewritten later.

New backend tests (`tests/test_auth.py`, `tests/test_ownership.py`):

- register → login → access a protected route
- duplicate registration is indistinguishable from a fresh one
- wrong password and unknown email return identical status, body, and comparable timing
- lockout engages after 5 failures and releases after the backoff
- access token expiry returns 401 with code `token_expired`
- a refresh rotates, and the old token then fails
- **replaying a used refresh token revokes the whole family** (the important one)
- `logout-all` invalidates every family
- password change revokes other families but not the current one
- ws-ticket is single-use, expires at 30 s, and an absent/invalid ticket closes with 4401
- **cross-account 404 on every session-scoped endpoint** (parameterise over the route list)
- `/progress` for user A never includes user B's sessions
- account deletion leaves zero rows across users, sessions, turns, refresh_tokens, ws_tickets,
  user_memory

New frontend tests:

- login form validation and error rendering
- boot refresh success renders the app, failure renders login
- a 401 triggers exactly one refresh and one retry under parallel requests
- logout clears user state and in-memory token
- sidebar groups by date and marks the active conversation
- selecting a conversation hydrates its turns and subsequent turns append to that session
- switching conversations while connected tears down the socket and capture

---

## 13. Phases

Ship in this order; each phase should leave the app runnable.

1. **Design canvas** — the seven screens from §2. Stop and show me.
2. **Model + Alembic** — users, tokens, tickets, memory, `sessions.user_id`, migrations.
3. **Auth service + routes** — hashing, tokens, rotation, reuse detection, rate limits.
4. **Ownership pass** — `deps.py`, every route scoped, `test_ownership.py` green.
5. **WebSocket tickets** — both sockets, plus the 4401 paths.
6. **Frontend auth** — router, context, memory-held token, boot refresh, single-flight retry.
7. **Auth screens** — login, register, settings, delete-account flow.
8. **Sidebar and resume** — the list, grouping, rename, delete-with-undo, `loadSession`.
9. **Memory** — only if §10 still reads as a good idea to you.
10. **Copy pass** through `humanizer`, then the full checklist.

---

## 14. Acceptance checklist

Report this back as a pass/fail list. Do not describe the work; run the checks.

- [ ] `cd backend && pytest` — all green, including the new suites
- [ ] `cd frontend && npm test && npm run build` — green, no TS errors
- [ ] Two accounts created; every session-scoped endpoint returns 404 across the boundary
- [ ] Server refuses to boot with `SCC_JWT_SECRET` unset and `debug=False`
- [ ] `grep -ri "localStorage\|sessionStorage" frontend/src` returns nothing token-related
- [ ] Replayed refresh token → family revoked → subsequent refresh 401
- [ ] WebSocket without a ticket closes 4401 before any audio frame is read
- [ ] Log output during a full login → speak → logout cycle contains no token, password, or
      transcript
- [ ] Account deletion leaves zero rows in all six tables
- [ ] Export returns every session and turn the account owns, and nothing else
- [ ] Sidebar: create three conversations, resume the second, confirm the new turn lands in
      that session and the coach answers with its prior context
- [ ] All new screens work at 360 px wide, in both themes, and under
      `prefers-reduced-motion: reduce`
- [ ] Every form is completable by keyboard alone, with visible focus throughout
- [ ] Screenshots of all seven screens in `design/screens/`

---

## 15. Anti-patterns

- Storing the access token in `localStorage` "for now"
- A `403` for someone else's session id
- `is_deleted = True` instead of deleting
- An admin role added "for debugging"
- A JWT in the WebSocket query string
- Composition rules on passwords ("must contain one uppercase and one symbol")
- `HTTP 500` with a stack trace reaching the browser
- A confirm dialog on every delete, and no undo anywhere
- "Oops! Something went wrong" — say what happened and what to do next
- An `Untitled conversation` created on every page load
- A memory feature the user cannot see, edit, or switch off
