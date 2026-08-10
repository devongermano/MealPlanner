import { PGlite } from '@electric-sql/pglite';
import { PGLiteSocketServer } from '@electric-sql/pglite-socket';
import { randomUUID } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';
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
  /**
   * Identifies THIS database. Jest runs suites in parallel workers, each with
   * its own instance on its own port, and a suite that reached another suite's
   * database would produce failures that look like application bugs. Checked by
   * `createTestApp`.
   */
  id: string;
  /** Empties every application table. Call between tests. */
  truncate(): Promise<void>;
  /** Runs SQL directly, bypassing Prisma — used by the RLS tests to switch role. */
  exec(sql: string): Promise<void>;
  query<T = Record<string, unknown>>(sql: string): Promise<T[]>;
  close(): Promise<void>;
}

const MIGRATIONS_DIR = join(__dirname, '..', '..', 'prisma', 'migrations');

const APP_TABLES = ['households', 'household_members', 'household_audit_log'];

/**
 * Not an application table — see `TestDatabase.id`. Lives in its own schema
 * rather than in `public` so it cannot weaken the RLS tests, which assert
 * that EVERY table in `public` has row-level security enabled. A test fixture
 * that forces an invariant to be stated as an exception list is a fixture
 * that will eventually hide a real missing policy.
 */
const MARKER_TABLE = 'harness.marker';

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

function isPortTaken(error: unknown): boolean {
  return (error as NodeJS.ErrnoException | undefined)?.code === 'EADDRINUSE';
}

/**
 * Binds a port by ATTEMPTING it, rather than probing for a free one first.
 *
 * The obvious version — listen on :0, read the port, close, hand it to the real
 * server — has a race: between the close and the real bind, a parallel Jest
 * worker doing the same thing can take that port. That produced exactly the
 * kind of flake this harness must not have, where one unrelated test in a
 * random suite fails a run. Here the only bind is the real one, so a collision
 * surfaces as EADDRINUSE and we simply try again.
 */
async function startServerOnFreePort(
  db: PGlite,
): Promise<{ server: PGLiteSocketServer; port: number }> {
  const attempts = 50;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const port = 20000 + Math.floor(Math.random() * 40000);
    const server = new PGLiteSocketServer({ db, port, host: '127.0.0.1' });
    try {
      await server.start();
      return { server, port };
    } catch (error) {
      await server.stop().catch(() => undefined);
      if (!isPortTaken(error)) throw error;
    }
  }
  throw new Error(`Could not find a free port after ${attempts} attempts`);
}

export async function startTestDatabase(): Promise<TestDatabase> {
  const db = await PGlite.create();
  const { server, port } = await startServerOnFreePort(db);

  for (const sql of migrationSql()) {
    await db.exec(sql);
  }

  const id = randomUUID();
  await db.exec(`
    CREATE SCHEMA harness;
    CREATE TABLE ${MARKER_TABLE} (id uuid PRIMARY KEY);
    INSERT INTO ${MARKER_TABLE} (id) VALUES ('${id}');
  `);

  return {
    // connection_limit=1 because PGlite serves one connection at a time.
    url: `postgresql://postgres:postgres@127.0.0.1:${port}/postgres?connection_limit=1`,
    id,
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

/**
 * Proves a Prisma client reached the database we started, not a neighbouring
 * worker's. Belt and braces alongside the bind-with-retry above: if a
 * cross-connection ever happens again it fails here, loudly and at setup, and
 * not as a puzzling assertion failure three suites away.
 */
export async function assertConnectedTo(
  database: TestDatabase,
  query: (sql: string) => Promise<Array<{ id: string }>>,
): Promise<void> {
  const rows = await query(`SELECT id FROM ${MARKER_TABLE}`);
  if (rows[0]?.id !== database.id) {
    throw new Error(
      `Test harness connected to the wrong database: expected marker ${database.id}, ` +
        `found ${String(rows[0]?.id)}. A parallel worker's server took this port.`,
    );
  }
}
