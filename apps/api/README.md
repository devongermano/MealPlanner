# apps/api

NestJS REST API — the "one brain": auth (Supabase JWT verify), authorization,
orchestration, and all persistence (Supabase Postgres via Prisma). Arrives at **M2**
per ARCHITECTURE.md; this directory is a placeholder until then so the monorepo shape
is real from M0. It stores and serves engine result objects verbatim from the private
Python solver service in `services/solver` — it never re-derives solver math (PRD P10).
