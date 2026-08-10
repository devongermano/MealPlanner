import { SetMetadata } from '@nestjs/common';

export const IS_PUBLIC_KEY = 'mealplan:isPublic';

/**
 * Opts a route out of authentication.
 *
 * Authentication is global (`APP_GUARD`), so a controller added later is
 * protected by default and exposing it is an explicit, greppable act. Reserve
 * this for probes that carry no household data.
 */
export const Public = () => SetMetadata(IS_PUBLIC_KEY, true);
