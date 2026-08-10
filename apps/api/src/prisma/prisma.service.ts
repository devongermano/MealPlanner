import { Inject, Injectable, Logger, OnModuleDestroy } from '@nestjs/common';
import { PrismaClient } from '@prisma/client';
import { API_CONFIG, type ApiConfig } from '../config';

/**
 * The Prisma client, wired to `DATABASE_URL` from validated config rather than
 * ambient `process.env`.
 *
 * Deliberately does NOT `$connect()` on module init. Prisma connects lazily on
 * first query, which keeps every no-database path working: unit tests, and the
 * OpenAPI dump that boots the real AppModule to read its routes. Fail-fast on a
 * bad connection string belongs on the readiness probe (`GET /readyz`), where
 * an orchestrator can act on it — not on process start, where it only turns a
 * transient database blip into a crash loop.
 *
 * NOTE ON RLS: this client connects as the schema owner, which Postgres exempts
 * from row-level security. That is intended — authorization is in this
 * application (ARCHITECTURE.md, "one brain"), and the policies in the migration
 * are a containment boundary for callers that bypass it. Nothing here should
 * ever set `request.jwt.claims`; doing so would start moving authorization into
 * the database.
 */
@Injectable()
export class PrismaService extends PrismaClient implements OnModuleDestroy {
  private readonly logger = new Logger(PrismaService.name);

  constructor(@Inject(API_CONFIG) config: ApiConfig) {
    super({ datasources: { db: { url: config.databaseUrl } } });
  }

  async onModuleDestroy(): Promise<void> {
    await this.$disconnect();
  }

  /** Backs `GET /readyz`. Cheap, and touches the real connection pool. */
  async ping(): Promise<boolean> {
    try {
      await this.$queryRaw`SELECT 1`;
      return true;
    } catch (error) {
      this.logger.error(
        'Database ping failed',
        error instanceof Error ? error.stack : String(error),
      );
      return false;
    }
  }
}
