import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter } from '@angular/router';

import { routes } from './app.routes';
import { AUTH_BACKEND } from './auth/auth-backend';
import { PreviewAuth } from './auth/preview-auth';
import { SupabaseAuth } from './auth/supabase-auth';
import { RUNTIME_CONFIG, type RuntimeConfig } from './config/runtime-config';
import { HOUSEHOLD_API } from './household/household-api';
import { HouseholdApiMock } from './household/household-api-mock';

/**
 * Built from the configuration fetched in main.ts, because which auth backend the
 * app uses is a runtime fact rather than a build-time one.
 *
 * HOUSEHOLD_API is the seam awaiting the NestJS endpoints: replacing the mock with
 * an HTTP implementation is a one-line change here and nothing else.
 */
export function createAppConfig(config: RuntimeConfig): ApplicationConfig {
  return {
    providers: [
      provideBrowserGlobalErrorListeners(),
      provideRouter(routes),
      { provide: RUNTIME_CONFIG, useValue: config },
      {
        provide: AUTH_BACKEND,
        useClass: config.authMode === 'supabase' ? SupabaseAuth : PreviewAuth,
      },
      // REGENERATE-FROM-CONTRACTS-API
      { provide: HOUSEHOLD_API, useClass: HouseholdApiMock },
    ],
  };
}
