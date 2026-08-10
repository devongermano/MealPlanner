import { inject } from '@angular/core';
import { Router, type CanActivateFn } from '@angular/router';
import { HouseholdStore } from './household-store';

/**
 * An account with no household has nothing to show in the shell — send it to
 * onboarding.
 *
 * A load that FAILED is not the same as one that came back empty. Treating them
 * alike would walk someone whose API is merely unreachable into creating a second
 * household they already own, so on error the shell renders and says what broke.
 */
export const requireHousehold: CanActivateFn = async () => {
  const store = inject(HouseholdStore);
  const router = inject(Router);
  await store.load();
  if (store.status() === 'error') {
    return true;
  }
  return store.hasHousehold() || router.createUrlTree(['/onboarding']);
};

/** Onboarding is a first-run flow; an account that already finished it belongs in the app. */
export const requireNoHousehold: CanActivateFn = async () => {
  const store = inject(HouseholdStore);
  const router = inject(Router);
  await store.load();
  // Same reasoning: while the load is broken we cannot know whether onboarding is
  // owed, so we must not start it.
  if (store.status() === 'error') {
    return router.createUrlTree(['/app']);
  }
  return !store.hasHousehold() || router.createUrlTree(['/app']);
};
