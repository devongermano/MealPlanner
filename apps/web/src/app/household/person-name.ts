/*
 * REGENERATE-FROM-CONTRACTS-API (partial)
 *
 * The engine keys every per-person map by the string under `people:` in the
 * library YAML — `jimbo`, not "Jimbo Smith". The API enforces that shape and is
 * the authority; this file exists only so the UI can offer a sensible default
 * and catch a bad value before a round trip.
 *
 * The pattern below is a deliberate copy of the API's PERSON_NAME_PATTERN. It is
 * a UX affordance, never the real check — the server rejects anything invalid
 * regardless. When packages/contracts-api exposes the rule, import it instead of
 * keeping a second copy.
 */

/** Mirror of the API's PERSON_NAME_PATTERN (apps/api/src/households/dto/household.dto.ts). */
const PERSON_NAME_PATTERN = /^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$/;

const MAX_LENGTH = 64;

export const PERSON_NAME_RULE =
  'Lowercase letters, numbers, dashes and underscores. Must start and end with a letter or number.';

export function isValidPersonName(value: string): boolean {
  return PERSON_NAME_PATTERN.test(value);
}

/**
 * Derive a plan identity from a display name: "Álex O'Brien" → "alex_obrien".
 * Returns null when nothing usable survives, which the caller treats as
 * "this person has no plan identity yet" rather than substituting a guess.
 */
export function slugifyPersonName(displayName: string): string | null {
  const slug = displayName
    .normalize('NFKD')
    // Strip combining marks so accented letters fold to their base form.
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, MAX_LENGTH)
    // Slicing can leave a trailing separator behind.
    .replace(/_+$/g, '');

  return slug && isValidPersonName(slug) ? slug : null;
}
