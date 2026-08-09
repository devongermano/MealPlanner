# packages/contracts

GENERATED TypeScript types + JSON schemas mirroring the engine's models — never
hand-edited. Arrives at the **start of M2** (the contract-codegen pipeline blocks all
other M2 API work per ARCHITECTURE.md): engine models → OpenAPI/JSON Schema emitted by
the solver service → codegen'd TS consumed by both `apps/api` and `apps/web`,
regenerated in CI with a drift test that fails the build. This directory is a
placeholder until then so the monorepo shape is real from M0.
