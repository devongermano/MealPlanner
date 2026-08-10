import type { components } from '@mealplan/contracts';

export type WeekPlanResult = components['schemas']['WeekPlanResult'];
export type Healthz = components['schemas']['Healthz'];

/**
 * A minimal WeekPlanResult-shaped literal, type-checked against the GENERATED
 * contracts package at compile time. This is the point of the scaffold: if the
 * pydantic -> OpenAPI -> TS pipeline changes shape, `nest build` breaks here —
 * proving apps/api consumes packages/contracts (ARCHITECTURE.md wiring rule).
 *
 * The values are an inert probe fixture, NOT engine output — the engine remains
 * the sole producer of real results (PRD P10); this file only names the shape.
 */
export const sampleWeekPlanResult: WeekPlanResult = {
  api_version: 'mealplan/v2',
  broke: { alice: { protein_g: 0 } },
  cost: {
    ceiling: null,
    eaten_value: { alice: 0 },
    groceries: 0,
    shares: { alice: 1 },
  },
  demand: { chili: 0 },
  feasible: true,
  library: {
    digest_sha256:
      '0000000000000000000000000000000000000000000000000000000000000000',
    n_components: 1,
    n_ingredients: 1,
    name: 'contracts-probe-fixture',
    people: ['alice'],
  },
  menu: ['chili'],
  menu_info: {
    active_min: 0,
    cuisines: 1,
    roles: { main: 1 },
    waste_perishable: 0,
  },
  relax_tiers: { alice: [0, null] },
  seed: 0,
  session_plan: {
    batches: { chili: 0 },
    freezer: [],
    leftover: [],
    minutes: 0,
    sessions: [],
    unattributed: [],
  },
  shopping: [
    {
      ingredient: 'beans',
      keeps_days: 365,
      leftover_g: 0,
      need_g: 0,
      pack_g: 400,
      perishable: false,
      units: 0,
    },
  ],
  volume: { alice: { avg_g: 0, cap_g: null, max_g: 0, min_g: 0 } },
  waste_perishable_g: 0,
  waste_total_g: 0,
  weeks: { alice: [{ chili: 0 }] },
};
