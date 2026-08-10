import { Controller, Get } from '@nestjs/common';
import type { Healthz, WeekPlanResult } from './contracts-sample';
import { sampleWeekPlanResult } from './contracts-sample';

@Controller()
export class AppController {
  /** Liveness probe. Same shape as the solver service's /healthz (contracts Healthz). */
  @Get('healthz')
  healthz(): Healthz {
    return { ok: true, api_version: 'mealplan/v2' };
  }

  /**
   * Compile-time proof that this app consumes packages/contracts: returns a
   * WeekPlanResult-typed fixture. Skeleton-only; removed when real solver
   * orchestration lands at M2 proper.
   */
  @Get('contracts-probe')
  contractsProbe(): WeekPlanResult {
    return sampleWeekPlanResult;
  }
}
