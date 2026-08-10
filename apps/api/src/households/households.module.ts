import { Module } from '@nestjs/common';
import { HouseholdMembershipGuard } from './household-membership.guard';
import { HouseholdsController } from './households.controller';
import { HouseholdsService } from './households.service';
import { MeController } from './me.controller';

@Module({
  controllers: [HouseholdsController, MeController],
  providers: [HouseholdsService, HouseholdMembershipGuard],
  exports: [HouseholdsService],
})
export class HouseholdsModule {}
