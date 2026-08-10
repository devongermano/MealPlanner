import { inject } from '@angular/core';
import { Router, type CanActivateFn } from '@angular/router';
import { HouseholdStore } from './household-store';

/** An account with no household has nothing to show in the shell — send it to onboarding. */
export const requireHousehold: CanActivateFn = async () => {
  const store = inject(HouseholdStore);
  const router = inject(Router);
  await store.load();
  return store.hasHousehold() || router.createUrlTree(['/onboarding']);
};

/** Onboarding is a first-run flow; an account that already finished it belongs in the app. */
export const requireNoHousehold: CanActivateFn = async () => {
  const store = inject(HouseholdStore);
  const router = inject(Router);
  await store.load();
  return !store.hasHousehold() || router.createUrlTree(['/app']);
};
