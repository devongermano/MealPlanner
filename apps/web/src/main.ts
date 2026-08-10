import { bootstrapApplication } from '@angular/platform-browser';
import { App } from './app/app';
import { createAppConfig } from './app/app.config';
import { loadRuntimeConfig } from './app/config/runtime-config';
import { CONFIG_PROBLEM, SetupNotice } from './app/config/setup-notice';

/**
 * Configuration is fetched before the app bootstraps, so which Supabase project to
 * authenticate against is a deploy-time fact rather than a build-time one. A config
 * we cannot use bootstraps a notice that says what to fix — never a blank page.
 */
async function start(): Promise<void> {
  const resolution = await loadRuntimeConfig();

  if (!resolution.ok) {
    await bootstrapApplication(SetupNotice, {
      providers: [{ provide: CONFIG_PROBLEM, useValue: resolution.problem }],
    });
    return;
  }

  await bootstrapApplication(App, createAppConfig(resolution.config));
}

start().catch((err) => console.error(err));
