import { Controller, Get } from '@nestjs/common';
import {
  ApiBearerAuth,
  ApiOperation,
  ApiResponse,
  ApiTags,
} from '@nestjs/swagger';
import type { AuthenticatedUser } from '../auth/authenticated-user';
import { CurrentUser } from '../auth/current-user.decorator';
import { ApiAuthenticatedErrors } from '../common/swagger';
import { HouseholdsService } from './households.service';
import { MeResponse } from './dto/household.dto';

/**
 * The web app's first authenticated call: who am I, and what can I see?
 *
 * Everything returned is derived from the verified token plus the caller's own
 * memberships — there is no path here that reads another account's data.
 */
@ApiTags('me')
@ApiBearerAuth()
@Controller('me')
export class MeController {
  constructor(private readonly households: HouseholdsService) {}

  @Get()
  @ApiOperation({
    summary: 'The verified caller and their household memberships',
  })
  @ApiResponse({ status: 200, type: MeResponse })
  @ApiAuthenticatedErrors()
  async me(@CurrentUser() user: AuthenticatedUser): Promise<MeResponse> {
    return {
      userId: user.userId,
      email: user.email,
      isAnonymous: user.isAnonymous,
      households: await this.households.listMine(user.userId),
    };
  }
}
