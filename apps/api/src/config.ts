/**
 * Env-driven config with sane defaults. Deliberately dependency-free for the
 * skeleton; swap for @nestjs/config at M2 proper if schema validation is wanted.
 */
export interface ApiConfig {
  /** Port the HTTP server binds. Env: PORT. */
  port: number;
  /** Host the HTTP server binds. Env: HOST. */
  host: string;
  /** Base URL of the (private) Python solver service. Env: SOLVER_URL. Unused until M2 proper. */
  solverUrl: string;
}

export const API_CONFIG = Symbol('API_CONFIG');

export function loadConfig(env: NodeJS.ProcessEnv = process.env): ApiConfig {
  return {
    port: Number(env.PORT ?? 3000),
    host: env.HOST ?? '0.0.0.0',
    solverUrl: env.SOLVER_URL ?? 'http://localhost:8000',
  };
}
