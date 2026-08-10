import { isApiErrorResponse } from '../app/errors/api-error';
import { FAKE_USER_ID, FakeHouseholdApi, fakeHousehold, fakeMember } from './fake-household-api';

/**
 * The double stands in for the real service in every component test, so its
 * authorization rules have to match. A double that is more permissive than the
 * API teaches the UI a habit that only fails in production.
 */
describe('FakeHouseholdApi authorization', () => {
  function api(): FakeHouseholdApi {
    return new FakeHouseholdApi(
      [fakeHousehold('hh-1', 'The Germanos')],
      [
        fakeMember('mem-1', 'Devon', 'planner', FAKE_USER_ID),
        fakeMember('mem-2', 'Alex', 'eater'),
        fakeMember('mem-3', 'Sam', 'cook', 'user-9'),
      ],
    );
  }

  it('lets a planner set role and plan identity on a member who has an account', async () => {
    const updated = await api().updateMember('hh-1', 'mem-3', {
      role: 'planner',
      personName: 'samuel',
    });

    expect(updated.role).toBe('planner');
    expect(updated.personName).toBe('samuel');
  });

  it("refuses a claimed member's display name, which belongs to their account", async () => {
    const cause = await api()
      .updateMember('hh-1', 'mem-3', { displayName: 'Samwise' })
      .catch((error: unknown) => error);

    expect(isApiErrorResponse(cause)).toBe(true);
    expect((cause as { error: { code: string } }).error.code).toBe('forbidden');
  });

  it('rejects a mixed patch WHOLE, applying neither field', async () => {
    const client = api();

    await client
      .updateMember('hh-1', 'mem-3', { role: 'planner', displayName: 'Samwise' })
      .catch(() => undefined);
    const [sam] = (await client.listMembers()).filter((member) => member.id === 'mem-3');

    // Not even the legal half — partial application would be a silent surprise.
    expect(sam.role).toBe('cook');
    expect(sam.displayName).toBe('Sam');
  });

  it('edits a placeholder freely, since nobody owns that row', async () => {
    const updated = await api().updateMember('hh-1', 'mem-2', {
      displayName: 'Alexandra',
      personName: 'alexandra',
    });

    expect(updated.displayName).toBe('Alexandra');
    expect(updated.personName).toBe('alexandra');
  });

  it('answers 409 when a plan identity is already taken in the household', async () => {
    const cause = await api()
      .updateMember('hh-1', 'mem-2', { personName: 'devon' })
      .catch((error: unknown) => error);

    expect((cause as { error: { code: string } }).error.code).toBe('conflict');
  });

  it('applies the self route without a role, and to the caller only', async () => {
    const updated = await api().updateSelf('hh-1', { displayName: 'Devon G' });

    expect(updated.id).toBe('mem-1');
    expect(updated.displayName).toBe('Devon G');
    expect(updated.role).toBe('planner');
  });
});
