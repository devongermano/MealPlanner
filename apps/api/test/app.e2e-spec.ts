import { Test, TestingModule } from '@nestjs/testing';
import { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { App } from 'supertest/types';
import { AppModule } from './../src/app.module';
import { sampleWeekPlanResult } from './../src/contracts-sample';

describe('AppController (e2e)', () => {
  let app: INestApplication<App>;

  beforeEach(async () => {
    const moduleFixture: TestingModule = await Test.createTestingModule({
      imports: [AppModule],
    }).compile();

    app = moduleFixture.createNestApplication();
    await app.init();
  });

  afterEach(async () => {
    await app.close();
  });

  it('/healthz (GET) returns the contracts Healthz shape', () => {
    return request(app.getHttpServer())
      .get('/healthz')
      .expect(200)
      .expect({ ok: true, api_version: 'mealplan/v2' });
  });

  it('/contracts-probe (GET) returns the WeekPlanResult-typed fixture', () => {
    return request(app.getHttpServer())
      .get('/contracts-probe')
      .expect(200)
      .expect(sampleWeekPlanResult);
  });
});
