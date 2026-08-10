import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { App } from './app';
import { RUNTIME_CONFIG, type RuntimeConfig } from './config/runtime-config';

const PREVIEW: RuntimeConfig = { authMode: 'preview', apiBaseUrl: 'http://localhost:3000' };
const SUPABASE: RuntimeConfig = {
  authMode: 'supabase',
  supabaseUrl: 'http://127.0.0.1:54321',
  supabaseAnonKey: 'anon-key',
  apiBaseUrl: 'http://localhost:3000',
};

async function render(config: RuntimeConfig): Promise<HTMLElement> {
  TestBed.configureTestingModule({
    imports: [App],
    providers: [provideRouter([]), { provide: RUNTIME_CONFIG, useValue: config }],
  });
  const fixture = TestBed.createComponent(App);
  await fixture.whenStable();
  fixture.detectChanges();
  return fixture.nativeElement as HTMLElement;
}

describe('App', () => {
  it('warns on every screen that preview accounts are not real', async () => {
    const element = await render(PREVIEW);

    expect(element.querySelector('.preview-banner')?.textContent).toContain('Preview mode');
  });

  it('shows no banner when authenticating against Supabase', async () => {
    const element = await render(SUPABASE);

    expect(element.querySelector('.preview-banner')).toBeNull();
  });
});
