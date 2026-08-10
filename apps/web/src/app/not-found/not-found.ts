import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'app-not-found',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
  template: `
    <main>
      <p class="eyebrow">404</p>
      <h1>That page is not on the menu</h1>
      <p class="muted">
        The link may be from an older version of the app, or it may simply be a typo.
      </p>
      <a class="btn" routerLink="/app">Back to your household</a>
    </main>
  `,
  styles: `
    main {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: var(--space-4);
      max-width: 34rem;
      margin: 0 auto;
      padding: var(--space-8) var(--space-4);
    }

    p.muted {
      max-width: 46ch;
    }
  `,
})
export class NotFound {}
