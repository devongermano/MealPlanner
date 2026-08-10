import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { Auth } from '../auth/auth';
import { HouseholdStore } from '../household/household-store';
import { Theme } from '../ui/theme';

/** Authenticated layout: identity, household context, navigation, outlet. */
@Component({
  selector: 'app-shell',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './shell.html',
  styleUrl: './shell.css',
})
export class Shell {
  private readonly auth = inject(Auth);
  private readonly store = inject(HouseholdStore);
  private readonly router = inject(Router);
  protected readonly theme = inject(Theme);

  protected readonly displayName = this.auth.displayName;
  protected readonly household = this.store.active;
  protected readonly households = this.store.households;
  protected readonly memberCount = computed(() => this.store.members().length);

  /** Multiple households are a later feature; until then the switcher shows context only. */
  protected readonly canSwitch = computed(() => this.households().length > 1);

  protected readonly initials = computed(() => {
    const name = this.displayName().trim();
    if (!name) {
      return '?';
    }
    const parts = name.split(/\s+/).slice(0, 2);
    return parts.map((part) => part[0]?.toUpperCase() ?? '').join('');
  });

  /**
   * A bare href="#main" resolves against the base href, so the router would leave
   * the current page instead of jumping to the content. Move focus directly.
   */
  protected skipToContent(event: Event): void {
    event.preventDefault();
    const main = document.getElementById('main');
    main?.focus();
    main?.scrollIntoView();
  }

  protected switchHousehold(event: Event): void {
    const id = (event.target as HTMLSelectElement).value;
    this.store.setActive(id);
    void this.store.reload();
  }

  protected async signOut(): Promise<void> {
    await this.auth.signOut();
    this.store.clear();
    await this.router.navigateByUrl('/login');
  }
}
