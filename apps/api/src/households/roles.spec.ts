import {
  HOUSEHOLD_ROLES,
  roleSatisfies,
  type HouseholdRoleName,
} from './roles';

describe('role ladder', () => {
  it('publishes exactly the three PRD §4.2 roles', () => {
    expect([...HOUSEHOLD_ROLES]).toEqual(['planner', 'cook', 'eater']);
  });

  /**
   * Exhaustive rather than illustrative: nine cases is small enough to state in
   * full, and a ladder is exactly the kind of thing that gets "simplified" into
   * an off-by-one.
   */
  const expected: Record<
    HouseholdRoleName,
    Record<HouseholdRoleName, boolean>
  > = {
    planner: { planner: true, cook: true, eater: true },
    cook: { planner: false, cook: true, eater: true },
    eater: { planner: false, cook: false, eater: true },
  };

  for (const actual of HOUSEHOLD_ROLES) {
    for (const required of HOUSEHOLD_ROLES) {
      it(`${actual} ${expected[actual][required] ? 'satisfies' : 'does not satisfy'} ${required}`, () => {
        expect(roleSatisfies(actual, required)).toBe(
          expected[actual][required],
        );
      });
    }
  }

  it('is reflexive — every role satisfies itself', () => {
    for (const role of HOUSEHOLD_ROLES) {
      expect(roleSatisfies(role, role)).toBe(true);
    }
  });

  it('is transitive: planner >= cook and cook >= eater implies planner >= eater', () => {
    expect(roleSatisfies('planner', 'cook')).toBe(true);
    expect(roleSatisfies('cook', 'eater')).toBe(true);
    expect(roleSatisfies('planner', 'eater')).toBe(true);
  });

  it('is antisymmetric for distinct roles — no two roles satisfy each other', () => {
    for (const a of HOUSEHOLD_ROLES) {
      for (const b of HOUSEHOLD_ROLES) {
        if (a === b) continue;
        expect(roleSatisfies(a, b) && roleSatisfies(b, a)).toBe(false);
      }
    }
  });
});
