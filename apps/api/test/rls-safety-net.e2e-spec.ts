import { startTestDatabase, type TestDatabase } from './harness/test-database';

/**
 * The RLS safety net, exercised rather than asserted by eyeball.
 *
 * ARCHITECTURE.md calls for "dumb household_id policies underneath Nest, never
 * business logic". Two things have to be true for that to mean anything, and
 * both are checked here:
 *
 *   1. The policies WORK. A caller arriving as the `authenticated` Data API
 *      role — a leaked publishable key, a stray PostgREST request, a Supabase
 *      client someone adds to the web app — sees only its own household's rows
 *      and can write nothing.
 *   2. The policies DO NOT bind the API. Nest connects as the table owner,
 *      which Postgres exempts from RLS. If that stopped being true, every
 *      query in the service would start depending on a session variable, and
 *      authorization would have quietly moved into the database.
 *
 * These tests use raw SQL rather than Prisma because they need to change the
 * Postgres role mid-connection, which is exactly the thing the application
 * must never do.
 */
describe('RLS safety net', () => {
  let database: TestDatabase;

  const HOUSEHOLD_A = '11111111-1111-1111-1111-111111111111';
  const HOUSEHOLD_B = '22222222-2222-2222-2222-222222222222';
  const USER_A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const USER_B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  const OUTSIDER = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';

  beforeAll(async () => {
    database = await startTestDatabase();

    // The Data API roles exist on Supabase and not on a bare Postgres, so the
    // migration's grants are guarded and skipped here. Recreate the role the
    // way Supabase provisions it, so what is under test is the POLICY.
    await database.exec(`
      CREATE ROLE authenticated NOLOGIN;
      GRANT USAGE ON SCHEMA public TO authenticated;
      GRANT SELECT ON households, household_members, household_audit_log TO authenticated;
    `);
  });

  afterAll(async () => {
    await database?.close();
  });

  beforeEach(async () => {
    await database.truncate();
    await database.exec(`
      INSERT INTO households (id, name, created_at, updated_at) VALUES
        ('${HOUSEHOLD_A}', 'Household A', now(), now()),
        ('${HOUSEHOLD_B}', 'Household B', now(), now());
      INSERT INTO household_members (id, household_id, user_id, role, display_name, created_at, updated_at) VALUES
        ('33333333-3333-4333-8333-333333333333', '${HOUSEHOLD_A}', '${USER_A}', 'planner', 'A Planner', now(), now()),
        ('44444444-4444-4444-8444-444444444444', '${HOUSEHOLD_B}', '${USER_B}', 'planner', 'B Planner', now(), now()),
        -- Placeholder members: real members of A and B with no account.
        ('77777777-7777-4777-8777-777777777771', '${HOUSEHOLD_A}', NULL, 'eater', 'A Placeholder', now(), now()),
        ('77777777-7777-4777-8777-777777777772', '${HOUSEHOLD_B}', NULL, 'eater', 'B Placeholder', now(), now());
      INSERT INTO household_audit_log (id, household_id, actor_user_id, action, created_at) VALUES
        ('55555555-5555-4555-8555-555555555555', '${HOUSEHOLD_A}', '${USER_A}', 'household.created', now()),
        ('66666666-6666-4666-8666-666666666666', '${HOUSEHOLD_B}', '${USER_B}', 'household.created', now());
    `);
  });

  /** Runs SQL as the Data API role would, with a JWT subject in scope. */
  async function asAuthenticated<T>(
    userId: string | null,
    sql: string,
  ): Promise<T[]> {
    const claims = userId === null ? "''" : `'{"sub":"${userId}"}'`;
    await database.exec(`SET LOCAL ROLE authenticated;`);
    await database.exec(
      `SET ROLE authenticated; SET request.jwt.claims = ${claims};`,
    );
    try {
      return await database.query<T>(sql);
    } finally {
      await database.exec(`RESET ROLE; RESET request.jwt.claims;`);
    }
  }

  describe('as the authenticated Data API role', () => {
    it('sees only households it is a member of', async () => {
      const rows = await asAuthenticated<{ name: string }>(
        USER_A,
        'SELECT name FROM households',
      );
      expect(rows.map((row) => row.name)).toEqual(['Household A']);
    });

    it('sees only membership rows of its own households', async () => {
      const rows = await asAuthenticated<{ display_name: string }>(
        USER_A,
        'SELECT display_name FROM household_members ORDER BY display_name',
      );
      // Its own household's placeholder is visible; the other household's is not.
      expect(rows.map((row) => row.display_name)).toEqual([
        'A Placeholder',
        'A Planner',
      ]);
    });

    /**
     * The whole placeholder design rests on this: a row with user_id NULL is a
     * member of the household but can never be a caller, because SQL's
     * `user_id = <uuid>` never matches NULL and nothing can make the current
     * subject NULL. If a placeholder ever granted access, every household with
     * one would be readable by anyone.
     */
    it('grants nothing through a placeholder member', async () => {
      // The placeholder's own row id, used as if it were a user id.
      const rows = await asAuthenticated(
        '77777777-7777-4777-8777-777777777771',
        'SELECT * FROM households',
      );
      expect(rows).toEqual([]);
    });

    it('sees only audit entries of its own households', async () => {
      const rows = await asAuthenticated<{ household_id: string }>(
        USER_B,
        'SELECT household_id FROM household_audit_log',
      );
      expect(rows.map((row) => row.household_id)).toEqual([HOUSEHOLD_B]);
    });

    it('sees nothing at all when it belongs to no household', async () => {
      expect(
        await asAuthenticated(OUTSIDER, 'SELECT * FROM households'),
      ).toEqual([]);
      expect(
        await asAuthenticated(OUTSIDER, 'SELECT * FROM household_members'),
      ).toEqual([]);
      expect(
        await asAuthenticated(OUTSIDER, 'SELECT * FROM household_audit_log'),
      ).toEqual([]);
    });

    it('sees nothing when no JWT subject is in scope', async () => {
      expect(await asAuthenticated(null, 'SELECT * FROM households')).toEqual(
        [],
      );
    });

    /**
     * The claims GUC is attacker-adjacent. A policy that RAISES on malformed
     * input instead of denying turns a refused read into a 500 and hands back
     * a signal about what got through. Every one of these must be a quiet
     * empty result.
     */
    it.each([
      ['empty claims', "''"],
      ['claims that are not JSON', "'not-json'"],
      ['JSON with no sub', `'{"role":"authenticated"}'`],
      ['a sub that is not a UUID', `'{"sub":"admin"}'`],
      ['a null sub', `'{"sub":null}'`],
      ['a sub that is an object', `'{"sub":{"nested":true}}'`],
    ])('denies quietly on %s', async (_label, claims) => {
      await database.exec(
        `SET ROLE authenticated; SET request.jwt.claims = ${claims};`,
      );
      try {
        await expect(
          database.query('SELECT * FROM households'),
        ).resolves.toEqual([]);
      } finally {
        await database.exec('RESET ROLE; RESET request.jwt.claims;');
      }
    });

    it('cannot reach another household by naming its id directly', async () => {
      const rows = await asAuthenticated(
        USER_A,
        `SELECT * FROM households WHERE id = '${HOUSEHOLD_B}'`,
      );
      expect(rows).toEqual([]);
    });

    /**
     * No write policy exists on any of these tables, so every write is denied
     * regardless of membership. Writes go through the API, which is the only
     * thing that records an audit entry alongside them.
     */
    it.each([
      [
        'insert a household',
        `INSERT INTO households (id, name, created_at, updated_at) VALUES ('77777777-7777-4777-8777-777777777777', 'Sneaky', now(), now())`,
      ],
      [
        'update its own household',
        `UPDATE households SET name = 'Renamed' WHERE id = '${HOUSEHOLD_A}'`,
      ],
      [
        'delete its own household',
        `DELETE FROM households WHERE id = '${HOUSEHOLD_A}'`,
      ],
      [
        'promote itself',
        `UPDATE household_members SET role = 'planner' WHERE user_id = '${USER_A}'`,
      ],
      [
        'forge an audit entry',
        `INSERT INTO household_audit_log (id, household_id, actor_user_id, action, created_at) VALUES ('88888888-8888-4888-8888-888888888888', '${HOUSEHOLD_A}', '${USER_A}', 'forged', now())`,
      ],
      [
        'delete an audit entry',
        `DELETE FROM household_audit_log WHERE household_id = '${HOUSEHOLD_A}'`,
      ],
    ])('cannot %s', async (_label, sql) => {
      await expect(asAuthenticated(USER_A, sql)).rejects.toThrow();
    });

    it('leaves the data untouched after every denied write', async () => {
      const households = await database.query<{ count: bigint }>(
        'SELECT count(*) FROM households',
      );
      expect(Number(households[0].count)).toBe(2);
    });
  });

  describe('as the owner, which is how the API connects', () => {
    it('is exempt from the policies, so the application is unaffected by them', async () => {
      const rows = await database.query<{ name: string }>(
        'SELECT name FROM households ORDER BY name',
      );
      expect(rows.map((row) => row.name)).toEqual([
        'Household A',
        'Household B',
      ]);
    });

    it('writes freely — RLS is a containment boundary, not the authorization layer', async () => {
      await database.exec(
        `INSERT INTO households (id, name, created_at, updated_at) VALUES ('99999999-9999-4999-8999-999999999999', 'C', now(), now())`,
      );
      const rows = await database.query<{ count: bigint }>(
        'SELECT count(*) FROM households',
      );
      expect(Number(rows[0].count)).toBe(3);
    });
  });

  describe('the migration itself', () => {
    it('leaves row-level security enabled on every application table', async () => {
      const rows = await database.query<{
        tablename: string;
        rowsecurity: boolean;
      }>(
        `SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename`,
      );
      expect(rows).toEqual([
        { tablename: 'household_audit_log', rowsecurity: true },
        { tablename: 'household_members', rowsecurity: true },
        { tablename: 'households', rowsecurity: true },
      ]);
    });

    it('defines SELECT policies and nothing else', async () => {
      const rows = await database.query<{ cmd: string }>(
        `SELECT cmd FROM pg_policies WHERE schemaname = 'public'`,
      );
      expect(rows).toHaveLength(3);
      expect(rows.every((row) => row.cmd === 'SELECT')).toBe(true);
    });

    it('does not FORCE row-level security, which would bind the owner too', async () => {
      const rows = await database.query<{
        relname: string;
        relforcerowsecurity: boolean;
      }>(
        `SELECT relname, relforcerowsecurity FROM pg_class
         WHERE relname IN ('households', 'household_members', 'household_audit_log')`,
      );
      expect(rows.every((row) => row.relforcerowsecurity === false)).toBe(true);
    });
  });
});
