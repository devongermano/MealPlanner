import type { INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import { AppModule } from '../../src/app.module';
import { buildValidationPipe } from '../../src/common/validation';
import { API_CONFIG, type ApiConfig } from '../../src/config';
import { PrismaService } from '../../src/prisma/prisma.service';
import { assertConnectedTo, type TestDatabase } from './test-database';
import { TEST_AUDIENCE, TEST_ISSUER, TEST_JWT_SECRET } from './tokens';

/**
 * Boots the real `AppModule` against the test database.
 *
 * `API_CONFIG` is overridden rather than mutating `process.env`, so parallel
 * test files cannot change each other's configuration — and so the test states
 * its configuration in one visible place instead of leaving it implicit in
 * environment order.
 *
 * The global validation pipe is applied here because `main.ts` applies it and
 * the tests must exercise the same request pipeline the server runs; a pipe
 * that only exists in production is a pipe nothing tests.
 *
 * Pass the `TestDatabase` (not a bare URL) and the app's own Prisma client is
 * checked against that database's marker before any test runs — so a suite can
 * never silently end up talking to a parallel worker's Postgres. A bare URL is
 * accepted only for the no-database smoke test, which has nothing to verify.
 */
export async function createTestApp(
  database: TestDatabase | string,
  overrides: Partial<ApiConfig> = {},
  /** Set false for suites that deliberately provoke logged errors. */
  logging = true,
): Promise<INestApplication> {
  const databaseUrl = typeof database === 'string' ? database : database.url;

  const config: ApiConfig = {
    port: 0,
    host: '127.0.0.1',
    solverUrl: 'http://localhost:8000',
    databaseUrl,
    docsEnabled: false,
    auth: {
      jwksUrl: null,
      jwtSecret: TEST_JWT_SECRET,
      issuer: TEST_ISSUER,
      audience: TEST_AUDIENCE,
      allowAnonymous: false,
      clockToleranceSec: 5,
    },
    ...overrides,
  };

  const moduleRef = await Test.createTestingModule({ imports: [AppModule] })
    .overrideProvider(API_CONFIG)
    .useValue(config)
    .compile();

  const app = moduleRef.createNestApplication({
    logger: logging ? undefined : false,
  });
  app.useGlobalPipes(buildValidationPipe());
  await app.init();

  if (typeof database !== 'string') {
    const prisma = prismaOf(app);
    await assertConnectedTo(database, (sql) =>
      prisma.$queryRawUnsafe<Array<{ id: string }>>(sql),
    );
  }

  return app;
}

export function prismaOf(app: INestApplication): PrismaService {
  return app.get(PrismaService);
}
