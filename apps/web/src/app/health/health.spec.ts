import { TestBed } from '@angular/core/testing';
import { Health } from './health';

describe('Health', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Health],
    }).compileComponents();
  });

  it('renders the contracts-typed /healthz shape', async () => {
    const fixture = TestBed.createComponent(Health);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="health-ok"]')?.textContent).toContain('true');
    expect(el.querySelector('[data-testid="health-api-version"]')?.textContent).toContain(
      'mealplan/v2',
    );
  });
});
