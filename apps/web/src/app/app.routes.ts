import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: 'health',
    loadComponent: () => import('./health/health').then((m) => m.Health),
  },
  { path: '', pathMatch: 'full', redirectTo: 'health' },
];
