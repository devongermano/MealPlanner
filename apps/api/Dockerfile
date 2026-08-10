# Multi-stage, slim. Build context is the REPO ROOT (pnpm workspace):
#   docker build -f apps/api/Dockerfile .
# (docker-compose.yml wires this up.)

FROM node:22.22.3-slim AS build
RUN corepack enable && corepack prepare pnpm@9.0.0 --activate
WORKDIR /repo
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml turbo.json ./
COPY packages/contracts ./packages/contracts
COPY apps/api ./apps/api
RUN pnpm install --frozen-lockfile --filter "@mealplan/api..."
RUN pnpm --filter @mealplan/api build
# Standalone deployable with prod deps only (workspace links resolved).
RUN pnpm --filter @mealplan/api --prod deploy /out

FROM node:22.22.3-slim AS runtime
ENV NODE_ENV=production
WORKDIR /app
COPY --from=build /out /app
EXPOSE 3000
USER node
CMD ["node", "dist/main.js"]
