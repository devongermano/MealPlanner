import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * Marks a surface that is intentionally not built yet, and says what it is waiting on.
 * Used instead of dead controls: a switch that does nothing is worse than an honest gap.
 */
@Component({
  selector: 'app-pending-note',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="pending">
      <div class="head">
        <h3>{{ heading() }}</h3>
        <span class="tag">{{ waitingOn() }}</span>
      </div>
      <p class="muted"><ng-content /></p>
    </section>
  `,
  styles: `
    .pending {
      padding: var(--space-4) var(--space-5);
      border: 1px dashed var(--border-strong);
      border-radius: var(--radius-md);
      background: color-mix(in srgb, var(--sunken) 60%, transparent);
    }

    .head {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--space-3);
      margin-bottom: var(--space-2);
    }

    p {
      font-size: var(--text-sm);
      max-width: 46ch;
    }
  `,
})
export class PendingNote {
  readonly heading = input.required<string>();
  readonly waitingOn = input('Coming with engine integration');
}
