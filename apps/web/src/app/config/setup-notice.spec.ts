import { TestBed } from '@angular/core/testing';
import type { ConfigProblem } from './runtime-config';
import { CONFIG_PROBLEM, SetupNotice, createSetupNoticeHost } from './setup-notice';

const PROBLEM: ConfigProblem = {
  headline: 'Supabase is not configured yet',
  detail: 'config.json selects the Supabase auth backend but supabaseAnonKey is empty.',
  steps: ['Run `supabase start`.', 'Paste the anon key into config.json.'],
};

describe('createSetupNoticeHost', () => {
  it('creates the element bootstrapApplication matches on, replacing the loading fallback', () => {
    const doc = document.implementation.createHTMLDocument('test');
    doc.body.innerHTML = '<app-root><p>Loading mealplan…</p></app-root>';

    const host = createSetupNoticeHost(doc);

    expect(host.tagName.toLowerCase()).toBe('app-setup-notice');
    expect(doc.body.children.length).toBe(1);
    expect(doc.body.firstElementChild).toBe(host);
    expect(doc.querySelector('app-root')).toBeNull();
  });
});

describe('SetupNotice', () => {
  it('renders the headline, the detail, and every remediation step', async () => {
    TestBed.configureTestingModule({
      imports: [SetupNotice],
      providers: [{ provide: CONFIG_PROBLEM, useValue: PROBLEM }],
    });

    const fixture = TestBed.createComponent(SetupNotice);
    await fixture.whenStable();
    fixture.detectChanges();
    const element = fixture.nativeElement as HTMLElement;

    expect(element.querySelector('h1')?.textContent).toContain('Supabase is not configured yet');
    expect(element.textContent).toContain('supabaseAnonKey is empty');
    expect(element.querySelectorAll('ol li').length).toBe(2);
  });
});
