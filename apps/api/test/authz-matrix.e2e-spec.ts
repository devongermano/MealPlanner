import type { INestApplication } from '@nestjs/common';
import request from 'supertest';
import type { App } from 'supertest/types';
import { PrismaService } from '../src/prisma/prisma.service';
import { createTestApp, prismaOf } from './harness/test-app';
import { startTestDatabase, type TestDatabase } from './harness/test-database';
import { errorOf } from './harness/response';
import { buildOpenApiDocument } from '../src/openapi';
import { newUserId, signAccessToken } from './harness/tokens';

/**
 * THE AUTHORIZATION MATRIX.
 *
 * Every household-scoped route × every kind of caller, against a real Postgres
 * with the real migration applied. This is the file to read when reviewing
 * whether this API leaks across households, and the file to extend before
 * adding any household-scoped route.
 *
 * The callers:
 *   planner / cook / eater — members of household A at each rung of the ladder
 *   outsider               — a planner, but of household B: the case that
 *                            matters most, because it has a valid token and
 *                            real privileges, just not here
 *   stranger               — authenticated, belongs to no household
 *   anonymous              — no token at all
 *
 * The expectation that carries the isolation guarantee (PRD §7): outsider and
 * stranger see 404 on every route of household A — never 403, which would
 * confirm the household exists.
 */

interface Actors {
  planner: string;
  cook: string;
  eater: string;
  outsider: string;
  stranger: string;
}

interface Seeded {
  householdA: string;
  householdB: string;
  plannerMemberId: string;
  cookMemberId: string;
  eaterMemberId: string;
  outsiderMemberId: string;
}

const USER_IDS = {
  planner: newUserId(),
  cook: newUserId(),
  eater: newUserId(),
  outsider: newUserId(),
  stranger: newUserId(),
};

describe('household authorization matrix (real Postgres)', () => {
  let database: TestDatabase;
  let app: INestApplication<App>;
  let prisma: PrismaService;
  let tokens: Actors;

  beforeAll(async () => {
    database = await startTestDatabase();
    app = (await createTestApp(database.url)) as INestApplication<App>;
    prisma = prismaOf(app);

    tokens = {
      planner: await signAccessToken({
        sub: USER_IDS.planner,
        email: 'planner@example.com',
      }),
      cook: await signAccessToken({ sub: USER_IDS.cook }),
      eater: await signAccessToken({ sub: USER_IDS.eater }),
      outsider: await signAccessToken({ sub: USER_IDS.outsider }),
      stranger: await signAccessToken({ sub: USER_IDS.stranger }),
    };
  });

  afterAll(async () => {
    await app?.close();
    await database?.close();
  });

  let seeded: Seeded;

  /**
   * Reseeded before every single case: the matrix contains destructive routes,
   * and a test that only passes because an earlier one already deleted the row
   * proves nothing.
   */
  beforeEach(async () => {
    await database.truncate();

    const householdA = await prisma.household.create({
      data: { name: 'Household A' },
    });
    const householdB = await prisma.household.create({
      data: { name: 'Household B' },
    });

    const [plannerMember, cookMember, eaterMember, outsiderMember] =
      await Promise.all([
        prisma.householdMember.create({
          data: {
            householdId: householdA.id,
            userId: USER_IDS.planner,
            role: 'planner',
            personName: 'devon',
          },
        }),
        prisma.householdMember.create({
          data: {
            householdId: householdA.id,
            userId: USER_IDS.cook,
            role: 'cook',
          },
        }),
        prisma.householdMember.create({
          data: {
            householdId: householdA.id,
            userId: USER_IDS.eater,
            role: 'eater',
            personName: 'jimbo',
          },
        }),
        prisma.householdMember.create({
          data: {
            householdId: householdB.id,
            userId: USER_IDS.outsider,
            role: 'planner',
          },
        }),
      ]);

    seeded = {
      householdA: householdA.id,
      householdB: householdB.id,
      plannerMemberId: plannerMember.id,
      cookMemberId: cookMember.id,
      eaterMemberId: eaterMember.id,
      outsiderMemberId: outsiderMember.id,
    };
  });

  type Caller = keyof Actors | 'anonymous';

  interface RouteCase {
    label: string;
    /** Issues the request as `caller`, against household A. */
    send: (caller: Caller) => request.Test;
    expected: Record<Caller, number>;
  }

  function call(
    method: 'get' | 'post' | 'patch' | 'delete',
    path: string,
    caller: Caller,
    body?: unknown,
  ) {
    let test = request(app.getHttpServer())[method](path);
    if (caller !== 'anonymous')
      test = test.set('Authorization', `Bearer ${tokens[caller]}`);
    if (body !== undefined) test = test.send(body as object);
    return test;
  }

  const routeCases = (): RouteCase[] => [
    {
      label: 'GET /households/:id',
      send: (caller) => call('get', `/households/${seeded.householdA}`, caller),
      expected: {
        planner: 200,
        cook: 200,
        eater: 200,
        outsider: 404,
        stranger: 404,
        anonymous: 401,
      },
    },
    {
      label: 'GET /households/:id/members',
      send: (caller) =>
        call('get', `/households/${seeded.householdA}/members`, caller),
      expected: {
        planner: 200,
        cook: 200,
        eater: 200,
        outsider: 404,
        stranger: 404,
        anonymous: 401,
      },
    },
    {
      label: 'PATCH /households/:id (rename)',
      send: (caller) =>
        call('patch', `/households/${seeded.householdA}`, caller, {
          name: 'Renamed',
        }),
      expected: {
        planner: 200,
        cook: 403,
        eater: 403,
        outsider: 404,
        stranger: 404,
        anonymous: 401,
      },
    },
    {
      label: 'DELETE /households/:id',
      send: (caller) =>
        call('delete', `/households/${seeded.householdA}`, caller),
      expected: {
        planner: 204,
        cook: 403,
        eater: 403,
        outsider: 404,
        stranger: 404,
        anonymous: 401,
      },
    },
    {
      label: 'POST /households/:id/members',
      send: (caller) =>
        call('post', `/households/${seeded.householdA}/members`, caller, {
          userId: newUserId(),
          role: 'eater',
        }),
      expected: {
        planner: 201,
        cook: 403,
        eater: 403,
        outsider: 404,
        stranger: 404,
        anonymous: 401,
      },
    },
    {
      label: 'PATCH /households/:id/members/:memberId',
      send: (caller) =>
        call(
          'patch',
          `/households/${seeded.householdA}/members/${seeded.eaterMemberId}`,
          caller,
          {
            role: 'cook',
          },
        ),
      expected: {
        planner: 200,
        cook: 403,
        eater: 403,
        outsider: 404,
        stranger: 404,
        anonymous: 401,
      },
    },
    {
      label: 'DELETE /households/:id/members/:memberId',
      send: (caller) =>
        call(
          'delete',
          `/households/${seeded.householdA}/members/${seeded.eaterMemberId}`,
          caller,
        ),
      expected: {
        planner: 204,
        cook: 403,
        eater: 403,
        outsider: 404,
        stranger: 404,
        anonymous: 401,
      },
    },
    {
      label: 'DELETE /households/:id/members/me (leave)',
      send: (caller) =>
        call('delete', `/households/${seeded.householdA}/members/me`, caller),
      // Any member may leave — including the cook and eater. The planner cannot,
      // because they are household A's only one: leaving would strand the
      // household with nobody able to administer it (409, not 403 — it is an
      // invariant, not a permission).
      expected: {
        planner: 409,
        cook: 204,
        eater: 204,
        outsider: 404,
        stranger: 404,
        anonymous: 401,
      },
    },
  ];

  for (const routeCase of routeCases()) {
    describe(routeCase.label, () => {
      const callers: Caller[] = [
        'planner',
        'cook',
        'eater',
        'outsider',
        'stranger',
        'anonymous',
      ];
      for (const caller of callers) {
        it(`${caller} -> ${routeCase.expected[caller]}`, async () => {
          await routeCase.send(caller).expect(routeCase.expected[caller]);
        });
      }
    });
  }

  describe('the 404 is not an existence oracle', () => {
    it('answers identically for a household that exists and one that does not', async () => {
      const real = await call(
        'get',
        `/households/${seeded.householdB}`,
        'stranger',
      ).expect(404);
      const imaginary = await call(
        'get',
        `/households/${newUserId()}`,
        'stranger',
      ).expect(404);

      expect(errorOf(real)).toEqual(errorOf(imaginary));
      expect(errorOf(real).code).toBe('not_found');
    });

    it('answers 404, not 403, for a member of another household', async () => {
      const response = await call(
        'get',
        `/households/${seeded.householdA}`,
        'outsider',
      ).expect(404);
      expect(errorOf(response).code).toBe('not_found');
      // The household's name must not appear anywhere in the response.
      expect(JSON.stringify(response.body)).not.toContain('Household A');
    });
  });

  describe('cross-household object references', () => {
    /**
     * The attack this closes: a planner has full rights in household B, and
     * passes one of B's member ids to a route scoped to household A — or vice
     * versa. Authorization is on the household in the path, so every child
     * object must also be proven to live in that household.
     */
    it('refuses to mutate a member of another household via a household you do administer', async () => {
      await call(
        'patch',
        `/households/${seeded.householdB}/members/${seeded.eaterMemberId}`,
        'outsider',
        { role: 'planner' },
      ).expect(404);

      const untouched = await prisma.householdMember.findUnique({
        where: { id: seeded.eaterMemberId },
      });
      expect(untouched?.role).toBe('eater');
      expect(untouched?.householdId).toBe(seeded.householdA);
    });

    it('refuses to delete a member of another household', async () => {
      await call(
        'delete',
        `/households/${seeded.householdB}/members/${seeded.cookMemberId}`,
        'outsider',
      ).expect(404);

      expect(
        await prisma.householdMember.findUnique({
          where: { id: seeded.cookMemberId },
        }),
      ).not.toBeNull();
    });

    it("refuses a member id from another household even when it is the caller's own", async () => {
      // The outsider's own membership row, addressed through household A.
      await call(
        'delete',
        `/households/${seeded.householdA}/members/${seeded.outsiderMemberId}`,
        'outsider',
      ).expect(404);

      expect(
        await prisma.householdMember.findUnique({
          where: { id: seeded.outsiderMemberId },
        }),
      ).not.toBeNull();
    });
  });

  describe('token handling on household routes', () => {
    const badHeaders: Array<[string, string]> = [
      ['no scheme', 'sometoken'],
      ['wrong scheme', 'Basic sometoken'],
      ['three parts', 'Bearer token extra'],
      ['empty credential', 'Bearer '],
      ['scheme only', 'Bearer'],
    ];

    for (const [label, header] of badHeaders) {
      it(`rejects ${label}`, async () => {
        await request(app.getHttpServer())
          .get(`/households/${seeded.householdA}`)
          .set('Authorization', header)
          .expect(401);
      });
    }

    it('accepts a lowercase "bearer" scheme', async () => {
      await request(app.getHttpServer())
        .get(`/households/${seeded.householdA}`)
        .set('Authorization', `bearer ${tokens.planner}`)
        .expect(200);
    });

    it('rejects an expired token even for a real member', async () => {
      const expired = await signAccessToken({
        sub: USER_IDS.planner,
        expiresInSeconds: -600,
      });
      await request(app.getHttpServer())
        .get(`/households/${seeded.householdA}`)
        .set('Authorization', `Bearer ${expired}`)
        .expect(401);
    });

    it('rejects a token signed with the wrong secret even for a real member', async () => {
      const forged = await signAccessToken({
        sub: USER_IDS.planner,
        secret: new TextEncoder().encode(
          'an-attacker-controlled-secret-32-chars',
        ),
      });
      await request(app.getHttpServer())
        .get(`/households/${seeded.householdA}`)
        .set('Authorization', `Bearer ${forged}`)
        .expect(401);
    });
  });

  describe('malformed identifiers', () => {
    it('rejects a non-UUID household id with 400, before touching the database', async () => {
      const response = await call(
        'get',
        '/households/not-a-uuid',
        'planner',
      ).expect(400);
      expect(errorOf(response).code).toBe('validation_failed');
    });

    it('rejects a non-UUID member id with 400', async () => {
      const response = await call(
        'patch',
        `/households/${seeded.householdA}/members/not-a-uuid`,
        'planner',
        { role: 'cook' },
      ).expect(400);
      expect(errorOf(response).code).toBe('validation_failed');
    });

    it('does not let a SQL-ish household id reach the database', async () => {
      await call('get', "/households/' OR 1=1 --", 'planner').expect(400);
      // Everything still standing.
      expect(await prisma.household.count()).toBe(2);
    });
  });
  /**
   * THE BACKSTOP.
   *
   * Everything above tests the routes that exist today. These two tests are for
   * the route somebody adds next year: they enumerate the API's OWN OpenAPI
   * document and assert the guarantees hold for every operation in it, so a new
   * household route that forgets `@UseGuards(HouseholdMembershipGuard)`, or a
   * new controller that quietly lands outside authentication, fails here rather
   * than in production.
   *
   * Adding a genuinely public route means adding it to PUBLIC_PATHS — a
   * deliberate, reviewable line in a diff, which is the point.
   */
  describe('no route escapes the guards', () => {
    const PUBLIC_PATHS = new Set(['/healthz', '/readyz', '/contracts-probe']);
    const METHODS = ['get', 'post', 'patch', 'delete'] as const;

    function operations(): Array<{
      method: (typeof METHODS)[number];
      path: string;
    }> {
      const document = buildOpenApiDocument(app);
      const found: Array<{ method: (typeof METHODS)[number]; path: string }> =
        [];
      for (const [path, item] of Object.entries(document.paths)) {
        for (const method of METHODS) {
          if (item[method]) found.push({ method, path });
        }
      }
      // A guard against the guard: if the document ever comes back empty, these
      // tests would vacuously pass.
      expect(found.length).toBeGreaterThan(8);
      return found;
    }

    function concrete(path: string): string {
      return path
        .replace('{householdId}', seeded.householdA)
        .replace('{memberId}', seeded.eaterMemberId);
    }

    it('every non-public operation rejects an unauthenticated request', async () => {
      const escaped: string[] = [];
      for (const { method, path } of operations()) {
        if (PUBLIC_PATHS.has(path)) continue;
        const response = await call(method, concrete(path), 'anonymous');
        if (response.status !== 401)
          escaped.push(`${method.toUpperCase()} ${path} -> ${response.status}`);
      }
      expect(escaped).toEqual([]);
    });

    it('every household-scoped operation answers 404 to a non-member', async () => {
      const escaped: string[] = [];
      for (const { method, path } of operations()) {
        if (!path.includes('{householdId}')) continue;
        for (const caller of ['outsider', 'stranger'] as const) {
          const response = await call(method, concrete(path), caller);
          if (response.status !== 404)
            escaped.push(
              `${caller} ${method.toUpperCase()} ${path} -> ${response.status}`,
            );
        }
      }
      expect(escaped).toEqual([]);
    });
  });
});
