import type { INestApplication } from '@nestjs/common';
import request from 'supertest';
import type { App } from 'supertest/types';
import { sampleWeekPlanResult } from '../src/contracts-sample';
import { createTestApp } from './harness/test-app';

/**
 * The no-database smoke test: routes that must answer even when Postgres is
 * unreachable, plus the contracts probe from Track C.
 *
 * It deliberately points at a database that does not exist. That is not a
 * shortcut — it is the assertion. Prisma connects lazily, so liveness and the
 * static probe have to keep working while the database is down; if either
 * starts touching it, this test fails and the readiness/liveness split has
 * quietly collapsed.
 */
describe('system routes without a database', () => {
  let app: INestApplication<App>;

  beforeAll(async () => {
    // Logging off: the readiness probe below is meant to fail, and its stack
    // trace on stderr would read like a broken test run.
    app = (await createTestApp(
      'postgresql://nobody:nobody@127.0.0.1:1/does-not-exist',
      {},
      false,
    )) as INestApplication<App>;
  });

  afterAll(async () => {
    await app?.close();
  });

  it('/healthz (GET) returns the contracts Healthz shape, unauthenticated', () => {
    return request(app.getHttpServer())
      .get('/healthz')
      .expect(200)
      .expect({ ok: true, api_version: 'mealplan/v2' });
  });

  it('/contracts-probe (GET) returns the WeekPlanResult-typed fixture', () => {
    return request(app.getHttpServer())
      .get('/contracts-probe')
      .expect(200)
      .expect(sampleWeekPlanResult);
  });

  it('/readyz (GET) reports 503 when the database is unreachable', async () => {
    const response = await request(app.getHttpServer())
      .get('/readyz')
      .expect(503);
    expect(response.body).toEqual({
      ok: false,
      database: false,
      authMode: 'shared-secret',
    });
  });

  it('every other route still demands a token', async () => {
    await request(app.getHttpServer()).get('/me').expect(401);
    await request(app.getHttpServer()).get('/households').expect(401);
  });
});
