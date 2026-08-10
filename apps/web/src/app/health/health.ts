import { Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { HealthData } from './health-data';

@Component({
  selector: 'app-health',
  template: `
    <section>
      <h2>API health</h2>
      @if (health(); as h) {
        <dl>
          <dt>ok</dt>
          <dd data-testid="health-ok">{{ h.ok }}</dd>
          <dt>api_version</dt>
          <dd data-testid="health-api-version">{{ h.api_version }}</dd>
        </dl>
      } @else {
        <p>Loading…</p>
      }
    </section>
  `,
})
export class Health {
  private readonly healthData = inject(HealthData);
  protected readonly health = toSignal(this.healthData.getHealth());
}
