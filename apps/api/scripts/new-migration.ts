/**
 * Authors a Prisma migration WITHOUT a database server, and gates the case
 * where someone edits the schema and forgets to.
 *
 * `prisma migrate dev` needs a live Postgres plus a shadow database to replay
 * history into, which would make a schema change require Docker. Instead the
 * previous datamodel is checked in as `prisma/schema.snapshot.prisma` and the
 * diff is datamodel-to-datamodel — the same `prisma migrate diff` engine, no
 * server involved.
 *
 *   new    <snake_case_name>   write the migration and advance the snapshot
 *   check                      exit 1 if schema.prisma has changes no migration
 *                              covers (CI runs this)
 *
 * The snapshot tracks the PRISMA MODEL only. Hand-written SQL — the RLS
 * policies, the conditional auth.users foreign key — is deliberately outside
 * Prisma's model and therefore outside the snapshot; it lives in the migration
 * files and is asserted by test/rls-safety-net.e2e-spec.ts.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

const API_DIR = resolve(__dirname, '..');
const MIGRATIONS_DIR = join(API_DIR, 'prisma', 'migrations');
const SCHEMA_PATH = join(API_DIR, 'prisma', 'schema.prisma');
const SNAPSHOT_PATH = join(API_DIR, 'prisma', 'schema.snapshot.prisma');

const NAME_PATTERN = /^[a-z0-9]+(?:_[a-z0-9]+)*$/;

const RLS_REMINDER = `
-- ---------------------------------------------------------------------------
-- RLS safety net — REQUIRED FOR EVERY NEW HOUSEHOLD-SCOPED TABLE
-- ---------------------------------------------------------------------------
-- A new table holding household data is not finished until it has the same
-- containment boundary as the others (ARCHITECTURE.md). Copy the pattern from
-- 20260809120000_households_and_membership/migration.sql section 3:
--
--   ALTER TABLE "<table>" ENABLE ROW LEVEL SECURITY;
--   CREATE POLICY "<table>_member_select" ON "<table>"
--     FOR SELECT USING (public.is_household_member("household_id"));
--   -- no write policies: writes go through the API
--   -- plus the guarded REVOKE/GRANT block for anon and authenticated
--
-- Then extend apps/api/test/rls-safety-net.e2e-spec.ts, which asserts the
-- policy set for every table in the public schema.
`;

/** Runs the diff with no shell, so no argument can become a command. */
function diffSnapshotToSchema(): string {
  return execFileSync(
    'npx',
    [
      'prisma',
      'migrate',
      'diff',
      '--from-schema-datamodel',
      SNAPSHOT_PATH,
      '--to-schema-datamodel',
      SCHEMA_PATH,
      '--script',
    ],
    {
      cwd: API_DIR,
      encoding: 'utf8',
      // The datasource block requires the variable to parse; nothing connects.
      env: {
        ...process.env,
        DATABASE_URL: 'postgresql://offline:offline@127.0.0.1:5432/offline',
      },
    },
  );
}

function isEmptyDiff(sql: string): boolean {
  return !sql.trim() || sql.includes('This is an empty migration');
}

/** UTC, matching Prisma's own migration directory convention. */
function timestamp(): string {
  return new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);
}

const SNAPSHOT_HEADER = `// GENERATED — never hand-edit, and never regenerate to "fix" a drift error.
// A copy of schema.prisma as of the newest migration, so migrations can be
// diffed offline. If \`prisma:migration:check\` fails, the answer is a new
// migration, not a new snapshot — advancing this file without the matching SQL
// tells CI a change shipped that no database has ever applied.
// Advanced by: pnpm --filter @mealplan/api prisma:migration:new <name>
`;

function writeSnapshot(): void {
  // Line comments do not affect the datamodel, so the header is free.
  writeFileSync(
    SNAPSHOT_PATH,
    `${SNAPSHOT_HEADER}\n${readFileSync(SCHEMA_PATH, 'utf8')}`,
    'utf8',
  );
}

function requireSnapshot(): void {
  if (existsSync(SNAPSHOT_PATH)) return;
  console.error(
    `Missing ${SNAPSHOT_PATH}. It must be the datamodel as of the newest migration; ` +
      'restore it from git rather than regenerating from the current schema.',
  );
  process.exit(2);
}

function commandNew(name: string | undefined): void {
  if (!name || !NAME_PATTERN.test(name)) {
    console.error(
      `usage: prisma:migration:new new <snake_case_name> (got ${JSON.stringify(name)})`,
    );
    process.exit(2);
  }

  const sql = diffSnapshotToSchema();
  if (isEmptyDiff(sql)) {
    console.log(
      'No schema changes — prisma/schema.prisma already matches the snapshot.',
    );
    return;
  }

  const dir = join(MIGRATIONS_DIR, `${timestamp()}_${name}`);
  mkdirSync(dir, { recursive: true });
  const path = join(dir, 'migration.sql');
  writeFileSync(path, `${sql.trimEnd()}\n${RLS_REMINDER}`, 'utf8');
  writeSnapshot();

  console.log(`Wrote ${path}`);
  console.log(`Advanced ${SNAPSHOT_PATH}`);
  console.log(
    'Next: add the RLS block for any new household-scoped table, then run the tests.',
  );
}

function commandCheck(): void {
  const sql = diffSnapshotToSchema();
  if (isEmptyDiff(sql)) {
    console.log('Schema and migrations agree.');
    return;
  }
  console.error(
    'DRIFT: prisma/schema.prisma has changes no migration covers. Run:\n' +
      '  pnpm --filter @mealplan/api prisma:migration:new new <name>\n\n' +
      'The missing SQL would be:\n',
  );
  console.error(sql);
  process.exit(1);
}

function main(): void {
  requireSnapshot();
  const [command, ...rest] = process.argv.slice(2);
  switch (command) {
    case 'new':
      return commandNew(rest[0]);
    case 'check':
      return commandCheck();
    default:
      console.error('usage: prisma:migration:new <new <name> | check>');
      process.exit(2);
  }
}

main();
