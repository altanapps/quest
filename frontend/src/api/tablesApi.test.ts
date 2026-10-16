import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchTables } from './tablesApi';
import { ApiClientError } from './request';

describe('Tables list API', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('uses cookie auth and encodes project and cursor query parameters', async () => {
    const response = { tables: [], has_more: false, next_cursor: null };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(response)));
    vi.stubGlobal('fetch', fetch);
    expect(await fetchTables({ projectId: 'a&b', limit: 5, cursor: 'stamp|id' })).toEqual(response);
    const [url, options] = fetch.mock.calls[0];
    expect(url).toContain('/tables?');
    const parsed = new URL(url, 'https://quest.test');
    expect(parsed.searchParams.get('project_id')).toBe('a&b');
    expect(parsed.searchParams.get('cursor')).toBe('stamp|id');
    expect(options.credentials).toBe('include');
  });
  it('preserves structured gate errors for the UI', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: { error: 'tables_disabled', message: 'Tables disabled' } }),
      { status: 403 },
    )));
    await expect(fetchTables({ shared: true })).rejects.toMatchObject({
      name: 'ApiClientError', statusCode: 403, errorCode: 'tables_disabled',
    } satisfies Partial<ApiClientError>);
  });
});
