import { Injectable, signal } from '@angular/core';

export type ThemeChoice = 'system' | 'light' | 'dark';

const STORAGE_KEY = 'mealplan.theme';
const ORDER: readonly ThemeChoice[] = ['system', 'light', 'dark'];

/**
 * Writes `data-theme` on the root element; the palette in styles.css does the rest.
 * 'system' removes the attribute so `prefers-color-scheme` takes over again.
 */
@Injectable({ providedIn: 'root' })
export class Theme {
  private readonly current = signal<ThemeChoice>(readStored());
  readonly choice = this.current.asReadonly();

  constructor() {
    this.apply(this.current());
  }

  cycle(): void {
    const next = ORDER[(ORDER.indexOf(this.current()) + 1) % ORDER.length];
    this.set(next);
  }

  set(choice: ThemeChoice): void {
    this.current.set(choice);
    this.apply(choice);
    try {
      localStorage.setItem(STORAGE_KEY, choice);
    } catch {
      // Theme is a preference, not state worth failing over.
    }
  }

  private apply(choice: ThemeChoice): void {
    const root = document.documentElement;
    if (choice === 'system') {
      root.removeAttribute('data-theme');
    } else {
      root.setAttribute('data-theme', choice);
    }
  }
}

function readStored(): ThemeChoice {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return ORDER.includes(stored as ThemeChoice) ? (stored as ThemeChoice) : 'system';
  } catch {
    return 'system';
  }
}
