# apps/web

Angular web app — the collaborative loop (household setup, propose → veto → lock →
deliver, day rebalance, pantry stock UI). Arrives at **M2** per ARCHITECTURE.md; this
directory is a placeholder until then so the monorepo shape is real from M0. It will
be a plain `ng` CLI workspace consuming generated types from `packages/contracts` and
talking only to the NestJS API in `apps/api`.
