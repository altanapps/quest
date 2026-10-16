import { describe, expect, it } from 'vitest';
import { parseTablesRoute, tablesListPath, tableViewerPath } from './tablesRoute';
import { TABLE_ID } from '../test/tableFixtures';

describe('Tables URLs', () => {
  it('parses list URLs and encoded project filters', () => {
    expect(parseTablesRoute('/tables', '')).toEqual({ kind: 'list', projectId: null });
    expect(parseTablesRoute('/tables/', '?project=')).toEqual({ kind: 'list', projectId: null });
    expect(parseTablesRoute('/tables', '?project=a%26b')).toEqual({ kind: 'list', projectId: 'a&b' });
    expect(tablesListPath('a&b')).toBe('/tables?project=a%26b');
  });
  it('round trips a canonical table id, tolerating one trailing slash', () => {
    expect(parseTablesRoute(tableViewerPath(TABLE_ID), '')).toEqual({ kind: 'viewer', tableId: TABLE_ID });
    expect(parseTablesRoute(`${tableViewerPath(TABLE_ID)}/`, '')).toEqual({ kind: 'viewer', tableId: TABLE_ID });
  });
  it.each(['/tables-extra', '/docs', '/tables/..', '/tables/%2F', '/tables/%', '/tables/not-an-id', `/tables/${TABLE_ID}/more`])(
    'rejects unrelated or malformed path %s', (path) => {
      expect(parseTablesRoute(path, '')).toBeNull();
    },
  );
});
