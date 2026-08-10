import type { INestApplication } from '@nestjs/common';
import request from 'supertest';
import type { App } from 'supertest/types';
import { PrismaService } from '../src/prisma/prisma.service';
import { createTestApp, prismaOf } from './harness/test-app';
import { startTestDatabase, type TestDatabase } from './harness/test-database';
import { bodyOf, errorOf, requestIdOf } from './harness/response';
import { newUserId, signAccessToken } from './harness/tokens';
import type {
  HouseholdMemberView,
  HouseholdSummary,
  MeResponse,
} from '../src/households/dto/household.dto';

/**
 * Behaviour of the household routes: the invariants, the audit trail, and the
 * shape of what comes back. The authorization matrix lives next door in
 * `authz-matrix.e2e-spec.ts`.
 */
describe('households (real Postgres)', () => {
  let database: TestDatabase;
  let app: INestApplication<App>;
  let prisma: PrismaService;

  const owner = newUserId();
  let ownerToken: string;

  beforeAll(async () => {
    database = await startTestDatabase();
    app = (await createTestApp(database)) as INestApplication<App>;
    prisma = prismaOf(app);
    ownerToken = await signAccessToken({
      sub: owner,
      email: 'owner@example.com',
    });
  });

  afterAll(async () => {
    await app?.close();
    await database?.close();
  });

  beforeEach(async () => {
    await database.truncate();
  });

  const asOwner = (method: 'get' | 'post' | 'patch' | 'delete', path: string) =>
    request(app.getHttpServer())
      [method](path)
      .set('Authorization', `Bearer ${ownerToken}`);

  async function createHousehold(name = 'The Germanos', personName?: string) {
    const response = await asOwner('post', '/households')
      .send({
        name,
        displayName: 'Owner',
        ...(personName ? { personName } : {}),
      })
      .expect(201);
    return response.body as {
      id: string;
      name: string;
      members: Array<{
        id: string;
        userId: string | null;
        role: string;
        displayName: string;
        personName: string | null;
      }>;
    };
  }

  describe('creation', () => {
    it('makes the creator a planner and returns the household with its one member', async () => {
      const household = await createHousehold('The Germanos', 'devon');

      expect(household.name).toBe('The Germanos');
      expect(household.members).toHaveLength(1);
      expect(household.members[0]).toMatchObject({
        userId: owner,
        role: 'planner',
        personName: 'devon',
      });
    });

    it('allows a creator who plans but does not eat', async () => {
      const household = await createHousehold('No-Eat Planner');
      expect(household.members[0].personName).toBeNull();
    });

    it('trims the name', async () => {
      const household = await createHousehold('   Padded   ');
      expect(household.name).toBe('Padded');
    });

    it('records the creation in the audit trail', async () => {
      const household = await createHousehold();

      const entries = await prisma.householdAuditEntry.findMany({
        where: { householdId: household.id },
      });
      expect(entries).toHaveLength(1);
      expect(entries[0]).toMatchObject({
        action: 'household.created',
        actorUserId: owner,
      });
    });
  });

  describe('listing', () => {
    it("returns only the caller's households, with their own role in each", async () => {
      const mine = await createHousehold('Mine');
      // A household the caller has nothing to do with.
      await prisma.household.create({
        data: {
          name: 'Someone Else',
          members: {
            create: {
              userId: newUserId(),
              role: 'planner',
              displayName: 'Someone Else',
            },
          },
        },
      });

      const response = await asOwner('get', '/households').expect(200);

      expect(response.body).toHaveLength(1);
      expect(bodyOf<HouseholdSummary[]>(response)[0]).toMatchObject({
        id: mine.id,
        name: 'Mine',
        role: 'planner',
        memberCount: 1,
      });
    });

    it('GET /me carries the verified identity and the same memberships', async () => {
      const household = await createHousehold('Mine');

      const response = await asOwner('get', '/me').expect(200);

      expect(response.body).toMatchObject({
        userId: owner,
        email: 'owner@example.com',
        isAnonymous: false,
      });
      expect(bodyOf<MeResponse>(response).households).toHaveLength(1);
      expect(bodyOf<MeResponse>(response).households[0].id).toBe(household.id);
    });

    it('returns an empty list rather than an error for an account with no households', async () => {
      const nobody = await signAccessToken({ sub: newUserId() });
      const response = await request(app.getHttpServer())
        .get('/households')
        .set('Authorization', `Bearer ${nobody}`)
        .expect(200);
      expect(response.body).toEqual([]);
    });
  });

  describe('the last-planner invariant', () => {
    it('refuses to demote the only planner', async () => {
      const household = await createHousehold();
      const memberId = household.members[0].id;

      const response = await asOwner(
        'patch',
        `/households/${household.id}/members/${memberId}`,
      )
        .send({ role: 'cook' })
        .expect(409);

      expect(errorOf(response).code).toBe('conflict');
      const unchanged = await prisma.householdMember.findUnique({
        where: { id: memberId },
      });
      expect(unchanged?.role).toBe('planner');
    });

    it('refuses to remove the only planner', async () => {
      const household = await createHousehold();
      await asOwner(
        'delete',
        `/households/${household.id}/members/${household.members[0].id}`,
      ).expect(409);
      expect(
        await prisma.householdMember.count({
          where: { householdId: household.id },
        }),
      ).toBe(1);
    });

    it('refuses to let the only planner leave', async () => {
      const household = await createHousehold();
      await asOwner('delete', `/households/${household.id}/members/me`).expect(
        409,
      );
    });

    it('allows the demotion once a second planner exists', async () => {
      const household = await createHousehold();
      await asOwner('post', `/households/${household.id}/members`)
        .send({
          userId: newUserId(),
          role: 'planner',
          displayName: 'Added Person',
        })
        .expect(201);

      await asOwner(
        'patch',
        `/households/${household.id}/members/${household.members[0].id}`,
      )
        .send({ role: 'eater' })
        .expect(200);

      expect(
        await prisma.householdMember.count({
          where: { householdId: household.id, role: 'planner' },
        }),
      ).toBe(1);
    });

    it('permits a non-planner to leave freely', async () => {
      const household = await createHousehold();
      const eater = newUserId();
      const eaterToken = await signAccessToken({ sub: eater });
      await asOwner('post', `/households/${household.id}/members`)
        .send({ userId: eater, role: 'eater', displayName: 'Added Person' })
        .expect(201);

      await request(app.getHttpServer())
        .delete(`/households/${household.id}/members/me`)
        .set('Authorization', `Bearer ${eaterToken}`)
        .expect(204);

      expect(
        await prisma.householdMember.count({
          where: { householdId: household.id },
        }),
      ).toBe(1);
    });
  });

  describe('membership', () => {
    /**
     * Invite intent must stay INTENT. The owner ruling allows an email on a
     * placeholder so a future invitation flow knows where to send, but it is
     * never resolved against the account directory — an endpoint that answers
     * "does this address have an account?" is an enumeration oracle, and
     * quietly linking a matching account would be that oracle with the answer
     * returned in the response body.
     */
    it('never resolves an invite email into an account, even a real one', async () => {
      const household = await createHousehold();

      const response = await asOwner(
        'post',
        `/households/${household.id}/members`,
      )
        .send({
          displayName: 'Alex',
          role: 'eater',
          // The OWNER's own address — an account that certainly exists.
          inviteEmail: 'owner@example.com',
        })
        .expect(201);

      const created = bodyOf<HouseholdMemberView>(response);
      // Still a placeholder: the address bought no account lookup.
      expect(created.userId).toBeNull();
      expect(created.inviteEmail).toBe('owner@example.com');

      // And it did not attach itself to the owner's existing membership.
      const members = await prisma.householdMember.findMany({
        where: { householdId: household.id },
      });
      expect(members).toHaveLength(2);
      expect(members.filter((m) => m.userId !== null)).toHaveLength(1);
    });

    it('refuses invite intent on a member that already has an account', async () => {
      const household = await createHousehold();
      const response = await asOwner(
        'post',
        `/households/${household.id}/members`,
      )
        .send({
          displayName: 'Alex',
          role: 'eater',
          userId: newUserId(),
          inviteEmail: 'alex@example.com',
        })
        .expect(400);
      expect(errorOf(response).code).toBe('validation_failed');
    });

    it('creates a placeholder member with no account when userId is omitted', async () => {
      const household = await createHousehold();
      const response = await asOwner(
        'post',
        `/households/${household.id}/members`,
      )
        .send({ displayName: 'Alex', role: 'eater', personName: 'alex' })
        .expect(201);

      expect(bodyOf<HouseholdMemberView>(response)).toMatchObject({
        userId: null,
        displayName: 'Alex',
        personName: 'alex',
        role: 'eater',
        inviteEmail: null,
      });
    });

    /**
     * Many placeholders per household is the point of the NULLS DISTINCT unique
     * index. If that index were ever changed to NULLS NOT DISTINCT, a household
     * could hold exactly one person without an account — which is the opposite
     * of the ruling.
     */
    it('allows many placeholder members in one household', async () => {
      const household = await createHousehold();
      for (const name of ['Alex', 'Sam', 'Robin']) {
        await asOwner('post', `/households/${household.id}/members`)
          .send({ displayName: name, role: 'eater' })
          .expect(201);
      }
      expect(
        await prisma.householdMember.count({
          where: { householdId: household.id, userId: null },
        }),
      ).toBe(3);
    });

    it('refuses to add the same account twice', async () => {
      const household = await createHousehold();
      const newcomer = newUserId();

      await asOwner('post', `/households/${household.id}/members`)
        .send({ userId: newcomer, role: 'eater', displayName: 'Added Person' })
        .expect(201);
      const response = await asOwner(
        'post',
        `/households/${household.id}/members`,
      )
        .send({ userId: newcomer, role: 'cook', displayName: 'Added Person' })
        .expect(409);

      expect(errorOf(response).code).toBe('conflict');
    });

    it('refuses to link two members to the same library person', async () => {
      const household = await createHousehold('The Germanos', 'devon');
      const response = await asOwner(
        'post',
        `/households/${household.id}/members`,
      )
        .send({
          userId: newUserId(),
          role: 'eater',
          displayName: 'Added Person',
          personName: 'devon',
        })
        .expect(409);
      expect(errorOf(response).message).toContain('devon');
    });

    /** The person key is scoped to the household, not global. */
    it('allows the same person name in a different household', async () => {
      await createHousehold('First', 'devon');
      const second = await createHousehold('Second', 'devon');
      expect(second.members[0].personName).toBe('devon');
    });

    it('unlinks my own person when personName is null', async () => {
      const household = await createHousehold('The Germanos', 'devon');
      // Through the SELF route: the owner has an account, so their profile
      // is theirs — a planner (even themselves) cannot edit it via the
      // planner route.
      const response = await asOwner(
        'patch',
        `/households/${household.id}/members/me`,
      )
        .send({ personName: null })
        .expect(200);
      expect(bodyOf<HouseholdMemberView>(response).personName).toBeNull();
    });

    it('lets a planner unlink a PLACEHOLDER member', async () => {
      const household = await createHousehold();
      const added = await asOwner('post', `/households/${household.id}/members`)
        .send({ displayName: 'Alex', role: 'eater', personName: 'alex' })
        .expect(201);

      const response = await asOwner(
        'patch',
        `/households/${household.id}/members/${bodyOf<HouseholdMemberView>(added).id}`,
      )
        .send({ personName: null })
        .expect(200);
      expect(bodyOf<HouseholdMemberView>(response).personName).toBeNull();
    });

    it('refuses an update with nothing in it', async () => {
      const household = await createHousehold();
      await asOwner(
        'patch',
        `/households/${household.id}/members/${household.members[0].id}`,
      )
        .send({})
        .expect(400);
    });

    it('audits a role change with both the before and the after', async () => {
      const household = await createHousehold();
      const target = newUserId();
      const added = await asOwner('post', `/households/${household.id}/members`)
        .send({ userId: target, role: 'eater', displayName: 'Added Person' })
        .expect(201);

      await asOwner(
        'patch',
        `/households/${household.id}/members/${bodyOf<HouseholdMemberView>(added).id}`,
      )
        .send({ role: 'cook' })
        .expect(200);

      const entry = await prisma.householdAuditEntry.findFirst({
        where: { householdId: household.id, action: 'member.updated' },
      });
      expect(entry?.detail).toMatchObject({
        before: { role: 'eater' },
        after: { role: 'cook' },
      });
    });
  });

  describe('deletion', () => {
    it('removes the household and its memberships but keeps the audit trail', async () => {
      const household = await createHousehold();
      await asOwner('delete', `/households/${household.id}`).expect(204);

      expect(await prisma.household.count()).toBe(0);
      expect(await prisma.householdMember.count()).toBe(0);

      // The record of what happened survives the thing it happened to.
      const entries = await prisma.householdAuditEntry.findMany({
        where: { householdId: household.id },
        orderBy: { createdAt: 'asc' },
      });
      expect(entries.map((entry) => entry.action)).toContain(
        'household.deleted',
      );
    });

    it('answers 404 afterwards', async () => {
      const household = await createHousehold();
      await asOwner('delete', `/households/${household.id}`).expect(204);
      await asOwner('get', `/households/${household.id}`).expect(404);
    });
  });

  describe('validation', () => {
    it('rejects an empty name', async () => {
      const response = await asOwner('post', '/households')
        .send({ name: '', displayName: 'X' })
        .expect(400);
      expect(errorOf(response).code).toBe('validation_failed');
      expect((errorOf(response).details ?? []).map((d) => d.field)).toEqual([
        'name',
      ]);
    });

    it('rejects a name over 120 characters', async () => {
      await asOwner('post', '/households')
        .send({ name: 'x'.repeat(121), displayName: 'X' })
        .expect(400);
    });

    it.each([
      'Devon',
      'has space',
      'trailing-',
      '-leading',
      'emoji🎉',
      'x'.repeat(65),
    ])('rejects the person name %j', async (personName) => {
      await asOwner('post', '/households')
        .send({ name: 'H', displayName: 'X', personName })
        .expect(400);
    });

    it.each(['devon', 'a', 'jimbo_2', 'kid-1', '9lives'])(
      'accepts the person name %j',
      async (personName) => {
        await asOwner('post', '/households')
          .send({ name: 'H', displayName: 'X', personName })
          .expect(201);
      },
    );

    it('rejects an unknown role', async () => {
      const household = await createHousehold();
      await asOwner('post', `/households/${household.id}/members`)
        .send({
          userId: newUserId(),
          role: 'admin',
          displayName: 'Added Person',
        })
        .expect(400);
    });

    /**
     * `forbidNonWhitelisted` is a privilege control, not tidiness: a body that
     * can smuggle an undeclared field is one refactor away from that field
     * being read.
     */
    it('rejects properties the DTO does not declare', async () => {
      const response = await asOwner('post', '/households')
        .send({ name: 'H', displayName: 'X', role: 'planner', isAdmin: true })
        .expect(400);
      expect(errorOf(response).code).toBe('validation_failed');
    });

    it('reports every failing field, not just the first', async () => {
      const household = await createHousehold();
      const response = await asOwner(
        'post',
        `/households/${household.id}/members`,
      )
        .send({
          userId: 'not-a-uuid',
          role: 'wizard',
          personName: 'Not A Slug',
          // displayName omitted: a fourth failure, and it must be reported
          // alongside the others rather than swallowed.
        })
        .expect(400);

      const fields = (errorOf(response).details ?? []).map((d) => d.field);
      expect(new Set(fields)).toEqual(
        new Set(['userId', 'role', 'personName', 'displayName']),
      );
    });
  });

  describe('error envelope', () => {
    it('is the same shape for every failure and carries a request id', async () => {
      const response = await asOwner(
        'get',
        `/households/${newUserId()}`,
      ).expect(404);

      expect(Object.keys(bodyOf<object>(response)).sort()).toEqual([
        'error',
        'requestId',
      ]);
      expect(errorOf(response).code).toBe('not_found');
      expect(typeof errorOf(response).message).toBe('string');
      expect(errorOf(response).message.length).toBeGreaterThan(0);
      expect(requestIdOf(response)).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
      );
    });

    it('gives a different request id per response', async () => {
      const first = await asOwner('get', `/households/${newUserId()}`).expect(
        404,
      );
      const second = await asOwner('get', `/households/${newUserId()}`).expect(
        404,
      );
      expect(requestIdOf(first)).not.toBe(requestIdOf(second));
    });
  });

  describe('system routes', () => {
    it('serves liveness without a token', async () => {
      await request(app.getHttpServer())
        .get('/healthz')
        .expect(200)
        .expect({ ok: true, api_version: 'mealplan/v2' });
    });

    it('reports readiness and the auth mode', async () => {
      const response = await request(app.getHttpServer())
        .get('/readyz')
        .expect(200);
      expect(response.body).toEqual({
        ok: true,
        database: true,
        authMode: 'shared-secret',
      });
    });
  });
});
