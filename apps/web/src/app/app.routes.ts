import { Routes } from '@angular/router';
import { requireGuest, requireSession } from './auth/auth-guard';
import { requireHousehold, requireNoHousehold } from './household/household-guard';

export const routes: Routes = [
  {
    path: 'login',
    title: 'Sign in — mealplan',
    canActivate: [requireGuest],
    loadComponent: () => import('./auth/login').then((m) => m.Login),
  },
  {
    path: 'signup',
    title: 'Create an account — mealplan',
    canActivate: [requireGuest],
    loadComponent: () => import('./auth/signup').then((m) => m.Signup),
  },
  {
    path: 'onboarding',
    title: 'Set up your household — mealplan',
    canActivate: [requireSession, requireNoHousehold],
    loadComponent: () => import('./onboarding/onboarding').then((m) => m.Onboarding),
  },
  {
    // Diagnostics page from the scaffold: proves the app consumes generated
    // contracts types. Unauthenticated on purpose — it is a health check.
    path: 'health',
    title: 'Health — mealplan',
    loadComponent: () => import('./health/health').then((m) => m.Health),
  },
  {
    path: '',
    canActivate: [requireSession, requireHousehold],
    loadComponent: () => import('./shell/shell').then((m) => m.Shell),
    children: [
      {
        path: 'app',
        title: 'mealplan',
        loadComponent: () => import('./dashboard/dashboard').then((m) => m.Dashboard),
      },
      {
        path: 'settings',
        title: 'Settings — mealplan',
        loadComponent: () => import('./settings/settings').then((m) => m.Settings),
      },
      { path: '', pathMatch: 'full', redirectTo: 'app' },
    ],
  },
  {
    path: '**',
    title: 'Not found — mealplan',
    loadComponent: () => import('./not-found/not-found').then((m) => m.NotFound),
  },
];
