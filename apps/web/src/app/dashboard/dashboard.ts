import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { Auth } from '../auth/auth';
import { HouseholdStore } from '../household/household-store';
import { PendingNote } from '../ui/pending-note';

/**
 * Placeholder dashboard. The plan / cook / eat surfaces belong to a later track —
 * the engine's result shapes are still moving, and building against them now would
 * mean rewriting them. What is real here is the household this account belongs to.
 */
@Component({
  selector: 'app-dashboard',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, PendingNote],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.css',
})
export class Dashboard {
  private readonly auth = inject(Auth);
  private readonly store = inject(HouseholdStore);

  protected readonly displayName = this.auth.displayName;
  protected readonly household = this.store.active;
  protected readonly members = this.store.members;

  protected readonly eaterCount = computed(() => this.members().length);

  protected readonly cookNames = computed(() =>
    this.members()
      .filter((member) => member.role === 'cook' || member.role === 'planner')
      .map((member) => member.displayName),
  );

  protected readonly isSolo = computed(() => this.members().length === 1);
}
