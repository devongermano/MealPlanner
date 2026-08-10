import { inject } from '@angular/core';
import { Router, type CanActivateFn } from '@angular/router';
import { Auth } from './auth';

/**
 * Both guards await `whenReady` first. Without it a reload races session restore
 * and bounces an already-signed-in user to /login before their token comes back.
 */
export const requireSession: CanActivateFn = async (_route, state) => {
  const auth = inject(Auth);
  const router = inject(Router);
  await auth.whenReady();
  return (
    auth.isSignedIn() || router.createUrlTree(['/login'], { queryParams: { next: state.url } })
  );
};

/** Keeps a signed-in user off /login and /signup. */
export const requireGuest: CanActivateFn = async () => {
  const auth = inject(Auth);
  const router = inject(Router);
  await auth.whenReady();
  return !auth.isSignedIn() || router.createUrlTree(['/app']);
};
