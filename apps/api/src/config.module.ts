import { Global, Module } from '@nestjs/common';
import { API_CONFIG, loadConfig } from './config';

/**
 * Provides the validated `ApiConfig` under `API_CONFIG`.
 *
 * Global because auth, Prisma, and the controllers all need it, and a config
 * value reachable from only some modules invites a second, unvalidated read of
 * `process.env` in the modules it does not reach.
 */
@Global()
@Module({
  providers: [{ provide: API_CONFIG, useFactory: () => loadConfig() }],
  exports: [API_CONFIG],
})
export class ConfigModule {}
