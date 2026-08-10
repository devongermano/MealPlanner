/**
 * Emits this API's OpenAPI document to a file.
 *
 * Boots the REAL AppModule and reads the routes Nest actually registered, so
 * the document cannot describe an API that does not exist. Nothing is written
 * to or read from a database: PrismaService connects lazily, so the app graph
 * builds without one.
 *
 * Usage:  ts-node scripts/dump-openapi.ts <output-path>
 * Called by packages/contracts-api/scripts/{gen,check}.sh — do not invoke it by
 * hand to "fix" the checked-in contract.
 */
import { writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { NestFactory } from '@nestjs/core';
import { AppModule } from '../src/app.module';
import { buildOpenApiDocument } from '../src/openapi';

/**
 * Config is validated at boot and this script has no real environment.
 * Placeholders keep the graph buildable; every value here is inert — the
 * process never opens a socket, verifies a token, or reads a row. They are set
 * only when absent so a caller can still point the dump at a real environment.
 */
const PLACEHOLDER_ENV: Record<string, string> = {
  DATABASE_URL: 'postgresql://openapi:openapi@127.0.0.1:5432/openapi',
  SUPABASE_JWT_SECRET: 'openapi-dump-placeholder-secret-not-a-real-key',
  SUPABASE_JWT_ISSUER: 'http://127.0.0.1:54321/auth/v1',
};

async function main(): Promise<void> {
  const outputArg = process.argv[2];
  if (!outputArg) {
    console.error('usage: ts-node scripts/dump-openapi.ts <output-path>');
    process.exit(2);
  }

  for (const [key, value] of Object.entries(PLACEHOLDER_ENV)) {
    if (!process.env[key]) process.env[key] = value;
  }

  // `logger: false` keeps Nest's startup banner out of the artifact pipeline.
  // `abortOnError: false` is not optional here: Nest's default is to call
  // process.abort() on a bootstrap failure and report it through the logger we
  // just disabled — which exits 1 with no output at all.
  const app = await NestFactory.create(AppModule, { logger: false, abortOnError: false });
  await app.init();

  const document = buildOpenApiDocument(app);
  // Trailing newline + 2-space indent: byte-identical to what the drift gate
  // regenerates, and diffable line-by-line in review.
  writeFileSync(resolve(outputArg), `${JSON.stringify(document, null, 2)}\n`, 'utf8');

  await app.close();
}

main().catch((error: unknown) => {
  console.error(error);
  process.exit(1);
});
