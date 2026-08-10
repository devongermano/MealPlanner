import { ChangeDetectionStrategy, Component, InjectionToken, inject } from '@angular/core';
import type { ConfigProblem } from './runtime-config';

export const CONFIG_PROBLEM = new InjectionToken<ConfigProblem>('CONFIG_PROBLEM');

/**
 * Bootstrapped in place of the app when configuration is unusable. This is the only
 * thing standing between a missing config.json and a white screen, so it says what
 * broke and what to type — never "something went wrong".
 */
@Component({
  selector: 'app-setup-notice',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <main>
      <article class="card">
        <p class="eyebrow">mealplan</p>
        <h1>{{ problem.headline }}</h1>
        <p class="detail">{{ problem.detail }}</p>
        <h2>To fix it</h2>
        <ol>
          @for (step of problem.steps; track step) {
            <li>{{ step }}</li>
          }
        </ol>
      </article>
    </main>
  `,
  styles: `
    main {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100dvh;
      padding: var(--space-5);
    }

    article {
      max-width: 34rem;
      padding: var(--space-6);
    }

    h1 {
      margin-top: var(--space-2);
      font-size: var(--text-2xl);
    }

    .detail {
      margin-top: var(--space-4);
      color: var(--ink-muted);
    }

    h2 {
      margin-top: var(--space-6);
      font-size: var(--text-sm);
      font-weight: 600;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--ink-faint);
    }

    ol {
      margin: var(--space-3) 0 0;
      padding-left: 1.15rem;
      display: flex;
      flex-direction: column;
      gap: var(--space-3);
    }

    li::marker {
      color: var(--ink-faint);
      font-variant-numeric: tabular-nums;
    }
  `,
})
export class SetupNotice {
  protected readonly problem = inject(CONFIG_PROBLEM);
}
