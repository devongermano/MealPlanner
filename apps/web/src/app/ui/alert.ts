import { ChangeDetectionStrategy, Component, input } from '@angular/core';

export type AlertTone = 'error' | 'info' | 'success';

/** Inline status message. Assertive for errors so a screen reader announces the failure. */
@Component({
  selector: 'app-alert',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div
      class="alert"
      [class.error]="tone() === 'error'"
      [class.success]="tone() === 'success'"
      [attr.role]="tone() === 'error' ? 'alert' : 'status'"
    >
      <ng-content />
    </div>
  `,
  styles: `
    .alert {
      padding: var(--space-3) var(--space-4);
      font-size: var(--text-sm);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      background: var(--sunken);
      color: var(--ink-muted);
    }

    .alert.error {
      border-color: color-mix(in srgb, var(--danger) 40%, transparent);
      background: var(--danger-soft);
      color: var(--danger);
    }

    .alert.success {
      border-color: color-mix(in srgb, var(--accent) 35%, transparent);
      background: var(--accent-soft);
      color: var(--accent);
    }
  `,
})
export class Alert {
  readonly tone = input<AlertTone>('info');
}
