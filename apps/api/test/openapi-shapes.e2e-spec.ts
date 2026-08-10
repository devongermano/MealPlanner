import type { INestApplication } from '@nestjs/common';
import type { OpenAPIObject } from '@nestjs/swagger';
import { buildOpenApiDocument } from '../src/openapi';
import { createTestApp } from './harness/test-app';

/**
 * The parts of a JSON Schema property this file inspects. Declared locally
 * rather than deep-imported from @nestjs/swagger's dist: that path is not in
 * the package's exports map, so it resolves under ts-jest and fails under
 * `tsc --moduleResolution nodenext`.
 */
interface PropertySchema {
  type?: string;
  nullable?: boolean;
  properties?: unknown;
  additionalProperties?: unknown;
  $ref?: string;
  allOf?: unknown;
  oneOf?: unknown;
  anyOf?: unknown;
}

/**
 * Guards the SHAPE of the generated contract, not its content.
 *
 * The bug that prompted this: `@ApiProperty({ nullable: true })` on a
 * `string | null` field. @nestjs/swagger cannot infer a type through that union
 * and silently emits `{"type": "object"}`, which openapi-typescript renders as
 * `Record<string, never> | null`. Every nullable field in the API was affected —
 * `userId`, `personName`, `inviteEmail`, `email` — so the web app could not
 * treat a person key as a string. It compiled, it generated, it passed every
 * behavioural test, and it was wrong.
 *
 * Nothing caught it because every other check asks "does the API do the right
 * thing?" These ask "is the contract we publish actually usable?" — a question
 * only the consumer was in a position to ask, until now.
 *
 * Needs no database: the document comes from the module graph.
 */
describe('generated OpenAPI shapes', () => {
  let app: INestApplication;
  let document: OpenAPIObject;

  beforeAll(async () => {
    app = await createTestApp(
      'postgresql://nobody:nobody@127.0.0.1:1/does-not-exist',
      {},
      false,
    );
    document = buildOpenApiDocument(app);
  });

  afterAll(async () => {
    await app?.close();
  });

  /** Every `Schema.property` pair in the document, flattened for assertion. */
  function properties(): Array<{ path: string; schema: PropertySchema }> {
    const out: Array<{ path: string; schema: PropertySchema }> = [];
    for (const [name, schema] of Object.entries(
      document.components?.schemas ?? {},
    )) {
      const props = (schema as { properties?: Record<string, PropertySchema> })
        .properties;
      for (const [property, definition] of Object.entries(props ?? {})) {
        out.push({ path: `${name}.${property}`, schema: definition });
      }
    }
    return out;
  }

  it('describes some schemas at all', () => {
    // Without this the assertions below would pass vacuously on an empty doc.
    expect(properties().length).toBeGreaterThan(20);
  });

  /**
   * A property typed `object` is only meaningful if it says what is IN the
   * object. One that does not is almost always a `type:` that @nestjs/swagger
   * could not infer — and it reaches the consumer as `Record<string, never>`,
   * a type nothing can be assigned to.
   */
  it('has no property typed as a structureless object', () => {
    const structureless = properties()
      .filter(({ schema }) => schema.type === 'object')
      .filter(
        ({ schema }) =>
          !schema.properties &&
          !schema.additionalProperties &&
          !schema.$ref &&
          !schema.allOf &&
          !schema.oneOf &&
          !schema.anyOf,
      )
      .map(({ path }) => path);

    expect(structureless).toEqual([]);
  });

  /**
   * The specific regression, stated as itself: nullable fields must still say
   * what they are when they are not null.
   */
  it('gives every nullable property a concrete type', () => {
    const untyped = properties()
      .filter(({ schema }) => schema.nullable === true)
      .filter(
        ({ schema }) => schema.type === undefined || schema.type === 'object',
      )
      .map(({ path }) => path);

    expect(untyped).toEqual([]);
  });

  it('types the fields the web app depends on as strings', () => {
    const byPath = new Map(
      properties().map(({ path, schema }) => [path, schema]),
    );
    for (const path of [
      'HouseholdMemberView.userId',
      'HouseholdMemberView.personName',
      'HouseholdMemberView.inviteEmail',
      'HouseholdSummary.personName',
      'MeResponse.email',
      'UpdateOwnMembershipRequest.personName',
      'UpdateHouseholdMemberRequest.personName',
      'UpdateHouseholdMemberRequest.inviteEmail',
    ]) {
      expect([path, byPath.get(path)?.type]).toEqual([path, 'string']);
    }
  });

  it('names every household route with the parameter the guard reads', () => {
    // HouseholdMembershipGuard resolves membership from `householdId`. A route
    // that scoped itself on any other parameter name would be unguarded.
    const scoped = Object.keys(document.paths).filter((path) =>
      path.startsWith('/households/{'),
    );
    expect(scoped.length).toBeGreaterThan(0);
    for (const path of scoped) {
      expect(path).toContain('{householdId}');
    }
  });
});
