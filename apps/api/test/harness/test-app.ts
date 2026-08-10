import type { INestApplication } from '@nestjs/common';
import { Test } from '@nestjs/testing';
import { AppModule } from '../../src/app.module';
import { buildValidationPipe } from '../../src/common/validation';
import { API_CONFIG, type ApiConfig } from '../../src/config';
import { PrismaService } from '../../src/prisma/prisma.service';
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
 */
export async function createTestApp(
  databaseUrl: string,
  overrides: Partial<ApiConfig> = {},
  /** Set false for suites that deliberately provoke logged errors. */
  logging = true,
): Promise<INestApplication> {
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
  return app;
}

export function prismaOf(app: INestApplication): PrismaService {
  return app.get(PrismaService);
}
