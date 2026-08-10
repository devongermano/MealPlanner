import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/** Split layout shared by sign-in and sign-up: the pitch on the left, the form on the right. */
@Component({
  selector: 'app-auth-shell',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="split">
      <aside>
        <p class="wordmark">mealplan</p>
        <p class="pitch">One week of food, planned together and cooked in a single afternoon.</p>
        <ul>
          <li>Everyone's macros come out of the same pots.</li>
          <li>Eaters veto before the plan locks, not after you have shopped.</li>
          <li>Cook days come with a plan, not a pile of recipes.</li>
        </ul>
      </aside>
      <main>
        <div class="panel card">
          <h1>{{ heading() }}</h1>
          <p class="muted lede">{{ subheading() }}</p>
          <ng-content />
        </div>
      </main>
    </div>
  `,
  styles: `
    .split {
      display: grid;
      grid-template-columns: 1fr;
      min-height: 100dvh;
    }

    aside {
      display: none;
      flex-direction: column;
      justify-content: center;
      gap: var(--space-5);
      padding: var(--space-8) var(--space-7);
      background: var(--sunken);
      border-right: 1px solid var(--border);
    }

    .wordmark {
      font-family: var(--font-serif);
      font-size: var(--text-xl);
      letter-spacing: -0.01em;
    }

    .pitch {
      max-width: 22ch;
      font-family: var(--font-serif);
      font-size: var(--text-3xl);
      line-height: 1.15;
      letter-spacing: -0.02em;
    }

    ul {
      display: flex;
      flex-direction: column;
      gap: var(--space-3);
      margin: 0;
      padding: 0;
      max-width: 34ch;
      list-style: none;
      font-size: var(--text-sm);
      color: var(--ink-muted);
    }

    li {
      padding-left: var(--space-4);
      border-left: 2px solid var(--border-strong);
    }

    main {
      display: flex;
      align-items: center;
      justify-content: center;
      padding: var(--space-6) var(--space-4);
    }

    .panel {
      width: 100%;
      max-width: 25rem;
      padding: var(--space-6);
    }

    h1 {
      font-size: var(--text-2xl);
    }

    .lede {
      margin-top: var(--space-2);
      font-size: var(--text-sm);
    }

    @media (min-width: 60rem) {
      .split {
        grid-template-columns: 1fr 1fr;
      }

      aside {
        display: flex;
      }
    }
  `,
})
export class AuthShell {
  readonly heading = input.required<string>();
  readonly subheading = input.required<string>();
}
