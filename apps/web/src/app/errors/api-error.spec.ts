import { describeApiError, isApiErrorResponse, toApiError } from './api-error';

describe('describeApiError', () => {
  it('never distinguishes forbidden from missing for a not_found', () => {
    const message = describeApiError({
      error: { code: 'not_found', message: 'Household not found.' },
      requestId: 'req-1',
    });

    // The API answers 404 for a household you are not a member of, on purpose.
    // Copy that says "you don't have access" would hand back the oracle.
    expect(message.toLowerCase()).not.toContain('access');
    expect(message.toLowerCase()).not.toContain('permission');
  });

  it('reports every failing field on a validation error, not just the first', () => {
    const message = describeApiError({
      error: {
        code: 'validation_failed',
        message: 'Invalid.',
        details: [
          { field: 'name', message: 'Name is required.' },
          { field: 'personName', message: 'personName must be a slug.' },
        ],
      },
      requestId: 'req-1',
    });

    expect(message).toContain('Name is required.');
    expect(message).toContain('personName must be a slug.');
  });

  it("keeps the server's specific wording for forbidden and conflict", () => {
    expect(
      describeApiError({
        error: { code: 'forbidden', message: 'displayName belongs to that account.' },
        requestId: 'req-1',
      }),
    ).toBe('displayName belongs to that account.');
  });

  it('shows copy this app authored verbatim rather than the generic line', () => {
    const authored = toApiError('unauthenticated', 'Preview mode has no API behind it.');

    // Otherwise a preview-mode refusal reads as "your session expired", which is
    // both wrong and unactionable.
    expect(describeApiError(authored)).toBe('Preview mode has no API behind it.');
  });

  it('falls back rather than throwing on something that is not an envelope', () => {
    expect(describeApiError(null, 'Fallback.')).toBe('Fallback.');
    expect(describeApiError(new Error('Boom.'))).toBe('Boom.');
  });
});

describe('isApiErrorResponse', () => {
  it('recognises the envelope and rejects near misses', () => {
    expect(isApiErrorResponse(toApiError('internal', 'x'))).toBe(true);
    expect(isApiErrorResponse({ error: 'nope' })).toBe(false);
    expect(isApiErrorResponse(null)).toBe(false);
  });
});
