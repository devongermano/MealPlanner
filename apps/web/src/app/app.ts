import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { RUNTIME_CONFIG } from './config/runtime-config';

@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterOutlet],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  /** Preview accounts are fake, so the app says so on every screen rather than once at sign-up. */
  protected readonly isPreview = inject(RUNTIME_CONFIG).authMode === 'preview';
}
