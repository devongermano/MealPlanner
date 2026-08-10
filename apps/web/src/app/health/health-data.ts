import { Injectable } from '@angular/core';
import { Observable, of } from 'rxjs';
import type { components } from '@mealplan/contracts';

/** Shape of the API's /healthz response — GENERATED contracts type, never hand-mirrored. */
export type Healthz = components['schemas']['Healthz'];

@Injectable({ providedIn: 'root' })
export class HealthData {
  /**
   * Mocked runtime call: the scaffold has no live backend. M2 proper replaces
   * the body with `HttpClient.get<Healthz>(`${apiBase}/healthz`)` — the return
   * type (from packages/contracts) stays exactly as it is here.
   */
  getHealth(): Observable<Healthz> {
    return of({ ok: true, api_version: 'mealplan/v2' });
  }
}
