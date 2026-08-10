import { isValidPersonName, slugifyPersonName } from './person-name';

describe('slugifyPersonName', () => {
  it('lowercases and joins words with underscores', () => {
    expect(slugifyPersonName('Jimbo Smith')).toBe('jimbo_smith');
  });

  it('folds accents to their base letters', () => {
    expect(slugifyPersonName('Álex')).toBe('alex');
  });

  it('drops punctuation rather than encoding it', () => {
    expect(slugifyPersonName("Alex O'Brien")).toBe('alex_o_brien');
  });

  it('never starts or ends with a separator', () => {
    expect(slugifyPersonName('  -Alex-  ')).toBe('alex');
  });

  it('returns null when nothing usable survives, instead of guessing', () => {
    expect(slugifyPersonName('***')).toBeNull();
    expect(slugifyPersonName('')).toBeNull();
  });

  it('produces a value the API pattern accepts, even from a long name', () => {
    const slug = slugifyPersonName('a'.repeat(200));

    expect(slug).not.toBeNull();
    expect(slug!.length).toBeLessThanOrEqual(64);
    expect(isValidPersonName(slug!)).toBe(true);
  });
});

describe('isValidPersonName', () => {
  it('accepts the library key shapes the engine uses', () => {
    expect(isValidPersonName('jimbo')).toBe(true);
    expect(isValidPersonName('alex-2')).toBe(true);
    expect(isValidPersonName('a_b')).toBe(true);
  });

  it('rejects anything that would not survive as a YAML key', () => {
    expect(isValidPersonName('Jimbo')).toBe(false);
    expect(isValidPersonName('_jimbo')).toBe(false);
    expect(isValidPersonName('jimbo_')).toBe(false);
    expect(isValidPersonName('jim bo')).toBe(false);
    expect(isValidPersonName('')).toBe(false);
  });
});
