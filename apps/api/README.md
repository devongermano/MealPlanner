# @mealplan/api

The NestJS REST API — **the one brain** (ARCHITECTURE.md). Authorization,
orchestration, and persistence live here. Nothing else talks to the database.

Current scope: **accounts, households, and roles**. No plan/solve/meal
endpoints yet — those shapes are still moving upstream in the engine.

---

## Quick start

```sh
cd apps/api
supabase start                 # Postgres + Auth (needs Docker)
cp .env.example .env           # then paste the JWT secret supabase printed
pnpm --filter @mealplan/api prisma:migrate:deploy
pnpm --filter @mealplan/api start:dev
```

```sh
curl localhost:3000/healthz    # liveness
curl localhost:3000/readyz     # readiness: database reachable, auth mode
```

**No Docker?** Everything except running the stack still works — the tests
bring their own Postgres (see [Testing](#testing)), migrations are authored
offline, and the contract is generated without a database. Only `supabase
start` needs Docker.

---

## Two migration systems, one owner each

| Schema | Owner | Tool |
|---|---|---|
| `public` | this app | `prisma migrate deploy` (`prisma/migrations/`) |
| `auth` | Supabase (GoTrue) | the Supabase stack itself |

`apps/api/supabase/` deliberately has **no `migrations/` directory**, and
`[db.migrations] enabled = false` in `config.toml`. Two migration tools pointed
at one schema is a merge conflict with a database attached.

Supabase config lives under `apps/api/` rather than the repo root because the
API is the only thing in the repo that talks to Postgres or Auth. Run every
`supabase` command from `apps/api`.

### Authoring a migration (no database required)

```sh
pnpm --filter @mealplan/api prisma:migration:new add_pantry_tables
```

`prisma migrate dev` would need a live Postgres plus a shadow database. Instead
the previous datamodel is checked in as `prisma/schema.snapshot.prisma` and the
diff is datamodel-to-datamodel — the same `prisma migrate diff` engine, no
server. The script advances the snapshot for you.

`prisma:migration:check` fails when `schema.prisma` has changes no migration
covers; CI runs it. **If it fails, write a migration — never just advance the
snapshot.**

Hand-written SQL (the RLS block, the conditional `auth.users` foreign key) sits
outside Prisma's model on purpose, so it lives in the migration files and is
asserted by `test/rls-safety-net.e2e-spec.ts`.

---

## Authentication

Supabase Auth (GoTrue) issues the tokens; this API only verifies them. Two
modes, and setting **both or neither is a boot error** — silently preferring one
is how a service ends up verifying against a key nobody believes it is using.

| Mode | Env | When |
|---|---|---|
| Asymmetric | `SUPABASE_JWKS_URL` | **Production.** ES256/RS256; this service holds no signing material, so compromising it cannot mint tokens. |
| Shared secret | `SUPABASE_JWT_SECRET` | Local. HS256; `supabase start` prints the secret. |

To use asymmetric keys locally, uncomment `signing_keys_path` in
`supabase/config.toml` and generate a key — it never gets committed:

```sh
supabase gen signing-key --algorithm ES256 > supabase/signing_keys.json
```

Three checks in `SupabaseJwtVerifier` are load-bearing, and removing any of them
is a privilege escalation:

1. **Algorithms are pinned per mode.** Otherwise an attacker takes the RSA
   public key published at the JWKS endpoint, signs a token with it as an HMAC
   secret, and it verifies.
2. **`role` must be `authenticated`.** Supabase's publishable *anon key* is
   itself a valid JWT signed with the same secret — it just carries
   `role: "anon"`. Without this check, a key printed in the web app's bundle
   authenticates as a user. The same check keeps `service_role` off user routes.
3. **`sub` must be a UUID.** It is the identity every household query keys on.

Anonymous sign-ins are rejected by default (`AUTH_ALLOW_ANONYMOUS=false`): a
household is durable state and an ephemeral identity should not create one.

---

## Members: placeholders and accounts

Owner ruling (2026-08-10): **a household must be fully plannable before every
member has an account.** PRD §4.2 iterates over people everywhere, and the
meal-prep model assumes you plan for someone who may never log in. So a
membership row is the PERSON; the auth identity is an attribute that may arrive
later.

| | `user_id` | Who owns it |
|---|---|---|
| **Placeholder** | `NULL` | The planner, entirely — display name, person, role, invite intent. |
| **Claimed** | set | The account owns its profile. A planner may change its **role** and remove it, nothing more. |

A placeholder **cannot authenticate**, and that is structural rather than
enforced: the verifier requires a UUID subject, and SQL's `user_id = <uuid>`
never matches NULL. No caller can ever resolve onto one.

Three fields, easily confused:

- `displayName` — what humans read. Required on every member.
- `personName` — the library/plan key (`alex`), the bridge to the engine.
- `inviteEmail` — where an invitation *would* go. **Intent only**: never checked
  against the account directory, because answering "does this address have an
  account?" is an enumeration oracle. Passing a real user's address creates a
  plain placeholder and links nothing.

### The claim seam (not built — blocked on PRD OQ-P1)

Claiming is an **UPDATE, never a delete-and-recreate**:

```sql
UPDATE household_members SET user_id = $1, invite_email = NULL
 WHERE id = $2 AND user_id IS NULL
```

The row `id` survives, which is what will let plans, portions, and veto history
reference a member before its account exists. The `(household_id, user_id)`
unique index makes that UPDATE safe: Postgres treats NULLs as **distinct**, so
any number of placeholders coexist while one account can never join a household
twice. **Do not change that index to `NULLS NOT DISTINCT`** — it would cap a
household at one person without an account.

## Roles and the authorization matrix

Roles are a **ladder, not a set**: `planner > cook > eater` (PRD §4.2). A
planner already holds every capability a cook holds, which is how "one person
can hold all roles" works with a single column.

| Route | Requires |
|---|---|
| `POST /households` | any authenticated account |
| `GET /households`, `GET /me` | any authenticated account |
| `GET /households/:id` | member (eater+) |
| `GET /households/:id/members` | member (eater+) |
| `PATCH /households/:id` | planner |
| `DELETE /households/:id` | planner |
| `POST /households/:id/members` | planner |
| `PATCH /households/:id/members/:memberId` | planner (role only, unless the target is a placeholder) |
| `DELETE /households/:id/members/:memberId` | planner |
| `PATCH /households/:id/members/me` | member (eater+), self only |
| `DELETE /households/:id/members/me` | member (eater+), self only |

Enforced in **one** place — `HouseholdMembershipGuard` — so there is one thing
to audit. `@MinRole` only ever raises the floor; membership is always required.

**Non-membership answers 404, never 403.** A 403 on a household you are not in
confirms that household exists, which is enough to enumerate households and to
confirm a guessed id. The message is identical to a genuine miss, because a
distinguishable body is the same oracle with extra steps.

Authentication is global (`APP_GUARD`), so a controller added later is protected
by default and exposing one is an explicit `@Public()` — greppable in one
command.

Self-edit is a separate route rather than a branch inside the planner handler,
and `UpdateOwnMembershipRequest` has **no `role` field**. That omission is the
control: the route is open to eaters, so a `role` there would be one-request
self-promotion. With `forbidNonWhitelisted`, sending one is a 400 rather than a
silent ignore.

The one rule the guard cannot express is target-dependent: a planner owns a
placeholder outright but may only change a claimed member's role. It lives in
`assertPlannerMayEdit` in the service, and answers 403.

---

## Row-level security is a safety net, not the authorization layer

The migration enables RLS with membership-only `SELECT` policies and **no write
policies at all**. Read the header of section 3 in the migration before changing
any of it. In short:

- Authorization is in this application. The policies exist for paths that reach
  Postgres *without* going through it — a leaked publishable key, a stray
  PostgREST request, a Supabase client someone adds to the web app later.
- Nest connects as the schema owner, which Postgres exempts from RLS (we do not
  set `FORCE ROW LEVEL SECURITY`). **Deleting every policy would not change one
  line of API behaviour** — if it did, business logic had leaked into the
  database, and that is the bug.
- Nothing in this app may ever set `request.jwt.claims`. That is the first step
  of moving authorization into the database.

`test/rls-safety-net.e2e-spec.ts` proves both halves: the policies bite for the
`authenticated` role, and they do not bind the owner.

---

## Testing

```sh
pnpm --filter @mealplan/api test
```

Tests run against **real Postgres with no Docker**: PGlite is Postgres compiled
to WebAssembly, and `PGLiteSocketServer` puts it behind the Postgres wire
protocol, so Prisma connects with an ordinary `postgresql://` URL and takes its
ordinary code path. The migration under test is the migration that ships, row
locks lock, and RLS policies are evaluated by the real planner.

Tokens are **really signed** with `jose` and verified by the real verifier — a
test that stubs verification proves the guard calls something, not that the
something rejects a forgery.

| Suite | Covers |
|---|---|
| `test/authz-matrix.e2e-spec.ts` | every household route × every kind of caller, cross-household object references, token handling, placeholder members, and self-edit-is-not-self-promotion |
| `test/households.e2e-spec.ts` | invariants (last planner, person uniqueness), the audit trail, validation, the error envelope |
| `test/rls-safety-net.e2e-spec.ts` | the policies, as SQL |
| `test/app.e2e-spec.ts` | liveness and the contracts probe **with the database down** |
| `src/**/*.spec.ts` | config validation, the role ladder, JWT verification (alg confusion, anon key, expiry, issuer, audience) |

**What this harness does not cover**, and what a running Supabase stack adds:

- GoTrue itself. Tokens are minted locally in the shape GoTrue emits; the login,
  refresh, and email flows are not exercised.
- The `auth.users` foreign key. The migration adds it only when the auth schema
  is present, so on a bare Postgres `household_members.user_id` is an
  unconstrained uuid.
- PostgREST. The RLS tests recreate the `authenticated` role and set the JWT
  claim GUCs the way PostgREST would, but PostgREST is not in the loop.

Those three are the reason to run the stack before shipping, and the honest
limit of a green test run here.

`--experimental-vm-modules` is in the `test` script because PGlite loads its
WebAssembly through a dynamic import, which Jest's sandbox blocks otherwise.

Each suite gets its own Postgres on its own port. The port is claimed by
**attempting the bind and retrying on `EADDRINUSE`**, never by probing for a
free port and binding it later — that gap lets a parallel Jest worker take the
port in between, which showed up as one unrelated test failing in roughly one
run in three. A per-database marker row is checked at startup so that if a
cross-connection ever happens again it fails immediately and by name.

---

## Contract discipline

`apps/api` **produces** `packages/contracts-api` and **consumes**
`packages/contracts`:

```
@mealplan/contracts       engine result models  ──► apps/api ──► @mealplan/contracts-api ──► apps/web
   (pydantic → OpenAPI)                            (Nest DTOs → OpenAPI)
```

The document is generated by booting the real `AppModule` and reading the routes
Nest registered, so it cannot describe an endpoint that does not exist. CI diffs
it against the checked-in copy; a controller or DTO change without regeneration
fails the PR. Never hand-edit `packages/contracts-api/src/index.ts`.

`API_DOCS=1` serves Swagger UI on `/docs`. Off by default — the generated
contract is what consumers build against, and a public route map helps nobody
except someone probing.

---

## Open questions for the owner

1. **Invitations are not built** (PRD OQ-P1, the notification channel). Until
   they are, a household is assembled from placeholder members and `inviteEmail`
   is inert storage. `POST /members` still accepts a `userId` for the case where
   the caller already knows one, but the web app has no way to produce one and
   should use placeholders.
2. **Roles are a single column, not a set.** The ladder covers PRD §4.2 as
   written. If a household ever needs "cook but explicitly not eater", it
   becomes a join table and a migration.
3. **No cap on households per account.** Any authenticated user can create
   unlimited households. Harmless at beta scale, but it is an unbounded write
   for anyone who can sign up. A cap is a product decision, not a default worth
   inventing.
4. **`person_name` is validated as a slug** (`^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$`),
   stricter than the engine, which validates nothing. Loosening later is a
   migration; tightening later is a data cleanup — so it starts strict.
5. **Audit entries are written but never read.** No endpoint exposes them yet.
   PRD §10 requires the trail; who gets to read it is unspecified.
6. **A planner cannot fix a claimed member's `personName`.** Under the ruling
   the account owns it, so if someone links themselves to the wrong library
   person only they can correct it. That is the intended reading of "self owns
   their profile", but it is worth confirming — `personName` is arguably
   planning data rather than profile data.
7. **`POST /members` with a `userId` adds that account without its consent.**
   It gains access to the household; it loses nothing. Invitations replace this
   path.
