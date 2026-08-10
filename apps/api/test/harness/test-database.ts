import { PGlite } from '@electric-sql/pglite';
import { PGLiteSocketServer } from '@electric-sql/pglite-socket';
import { readFileSync, readdirSync } from 'node:fs';
import { createServer } from 'node:net';
import type { AddressInfo } from 'node:net';
import { join } from 'node:path';

/**
 * A real Postgres for tests, with no Docker and no shared state.
 *
 * PGlite is Postgres itself compiled to WebAssembly, and `PGLiteSocketServer`
 * puts it behind the Postgres wire protocol — so Prisma connects with an
 * ordinary `postgresql://` URL and takes its ordinary code path. Nothing here
 * is a stub: the migration under test is the migration that ships, row locks
 * lock, and the RLS policies are evaluated by the same planner that will
 * evaluate them in production.
 *
 * This is what lets the authorization matrix be tested against real queries
 * instead of a mocked repository, which is the only way a test can catch a
 * missing `WHERE household_id = …`.
 *
 * What it does NOT cover, and what a Supabase stack would add: GoTrue itself
 * (so tokens here are minted locally rather than by a login flow) and the
 * `auth.users` foreign key, which the migration skips when the auth schema is
 * absent. See `apps/api/README.md`.
 */
export interface TestDatabase {
  /** Connection string for Prisma. */
  url: string;
  /** Empties every application table. Call between tests. */
  truncate(): Promise<void>;
  /** Runs SQL directly, bypassing Prisma — used by the RLS tests to switch role. */
  exec(sql: string): Promise<void>;
  query<T = Record<string, unknown>>(sql: string): Promise<T[]>;
  close(): Promise<void>;
}

const MIGRATIONS_DIR = join(__dirname, '..', '..', 'prisma', 'migrations');

const APP_TABLES = ['households', 'household_members', 'household_audit_log'];

async function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.on('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address() as AddressInfo;
      probe.close(() => resolve(port));
    });
  });
}

/**
 * Applies every checked-in migration in filename order — the same order
 * `prisma migrate deploy` uses. Reading the directory rather than naming one
 * file means a migration added later is covered by these tests automatically,
 * including its RLS changes.
 */
function migrationSql(): string[] {
  return readdirSync(MIGRATIONS_DIR, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort()
    .map((name) =>
      readFileSync(join(MIGRATIONS_DIR, name, 'migration.sql'), 'utf8'),
    );
}

export async function startTestDatabase(): Promise<TestDatabase> {
  const db = await PGlite.create();
  const port = await findFreePort();
  const server = new PGLiteSocketServer({ db, port, host: '127.0.0.1' });
  await server.start();

  for (const sql of migrationSql()) {
    await db.exec(sql);
  }

  return {
    // connection_limit=1 because PGlite serves one connection at a time.
    url: `postgresql://postgres:postgres@127.0.0.1:${port}/postgres?connection_limit=1`,
    async truncate() {
      await db.exec(
        `TRUNCATE ${APP_TABLES.join(', ')} RESTART IDENTITY CASCADE;`,
      );
    },
    async exec(sql: string) {
      await db.exec(sql);
    },
    async query<T = Record<string, unknown>>(sql: string) {
      const result = await db.query<T>(sql);
      return result.rows;
    },
    async close() {
      await server.stop();
      await db.close();
    },
  };
}
