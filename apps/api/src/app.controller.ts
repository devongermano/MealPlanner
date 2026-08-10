import { Controller, Get, HttpCode, HttpStatus, Res } from '@nestjs/common';
import { ApiOperation, ApiResponse, ApiTags } from '@nestjs/swagger';
import type { Response } from 'express';
import { Public } from './auth/public.decorator';
import { SupabaseJwtVerifier } from './auth/supabase-jwt.verifier';
import { PrismaService } from './prisma/prisma.service';
import type { Healthz, WeekPlanResult } from './contracts-sample';
import { sampleWeekPlanResult } from './contracts-sample';
import { ReadyzResponse } from './readyz.dto';

@ApiTags('system')
@Controller()
export class AppController {
  constructor(
    private readonly prisma: PrismaService,
    private readonly verifier: SupabaseJwtVerifier,
  ) {}

  /** Liveness probe. Same shape as the solver service's /healthz (contracts Healthz). */
  @Public()
  @Get('healthz')
  @ApiOperation({ summary: 'Liveness. The process is up; says nothing about its dependencies.' })
  healthz(): Healthz {
    return { ok: true, api_version: 'mealplan/v2' };
  }

  /**
   * Readiness. Separate from liveness on purpose: a database blip should take
   * this instance out of rotation, not restart it.
   *
   * Reports which verification mode auth is in, which is the one config
   * mistake that is invisible until someone cannot log in. It reveals no
   * secret — only which of two well-known modes is active.
   */
  @Public()
  @Get('readyz')
  @ApiOperation({ summary: 'Readiness: database reachable, auth verifier configured.' })
  @ApiResponse({ status: 200, type: ReadyzResponse })
  @ApiResponse({ status: 503, type: ReadyzResponse, description: 'Database unreachable.' })
  async readyz(@Res({ passthrough: true }) response: Response): Promise<ReadyzResponse> {
    const database = await this.prisma.ping();
    if (!database) response.status(HttpStatus.SERVICE_UNAVAILABLE);
    return { ok: database, database, authMode: this.verifier.mode };
  }

  /**
   * Compile-time proof that this app consumes packages/contracts: returns a
   * WeekPlanResult-typed fixture. Skeleton-only; removed when real solver
   * orchestration lands at M2 proper.
   */
  @Public()
  @Get('contracts-probe')
  @HttpCode(HttpStatus.OK)
  @ApiOperation({ summary: 'Inert fixture proving apps/api compiles against @mealplan/contracts.' })
  contractsProbe(): WeekPlanResult {
    return sampleWeekPlanResult;
  }
}
