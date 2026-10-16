import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClientError } from '../api/request';
import type { ListTablesResponse } from '../api/tablesApi';
import { tableFixture } from '../test/tableFixtures';
import { TABLES_REFRESH_DEBOUNCE_MS, useTables, type UseTablesOptions } from './useTables';

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  listeners: new Set<(event: { type: string }) => void>(),
}));
vi.mock('../api/tablesApi', async (original) => ({
  ...await original<typeof import('../api/tablesApi')>(), fetchTables: mocks.fetch,
}));
vi.mock('../services/PersistentWebSocket', () => ({
  persistentWebSocket: {
    onGlobalEvent: (listener: (event: { type: string }) => void) => {
      mocks.listeners.add(listener);
      return () => mocks.listeners.delete(listener);
    },
  },
}));

function page(ids: string[], cursor: string | null = null): ListTablesResponse {
  return {
    tables: ids.map((id) => tableFixture({ id })),
    has_more: cursor !== null, next_cursor: cursor,
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
function emit(type = 'table_list_changed') {
  for (const listener of mocks.listeners) listener({ type });
}

describe('useTables', () => {
  beforeEach(() => { mocks.fetch.mockReset(); mocks.listeners.clear(); });
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it('paginates with independent cursors and deduplicates overlaps', async () => {
    mocks.fetch.mockResolvedValueOnce(page(['a', 'b'], 'next'));
    const { result } = renderHook(() => useTables({ limit: 2 }));
    await waitFor(() => expect(result.current.tables).toHaveLength(2));
    mocks.fetch.mockResolvedValueOnce(page(['b', 'c']));
    act(() => { result.current.loadMore(); result.current.loadMore(); });
    await waitFor(() => expect(result.current.tables.map((t) => t.id)).toEqual(['a', 'b', 'c']));
    expect(mocks.fetch).toHaveBeenCalledTimes(2);
    expect(mocks.fetch).toHaveBeenLastCalledWith({ projectId: null, limit: 2, cursor: 'next' });
    expect(result.current.hasMore).toBe(false);
  });

  it('never fetches or subscribes while disabled and drops in-flight results when disabled', async () => {
    const pending = deferred<ListTablesResponse>();
    mocks.fetch.mockReturnValue(pending.promise);
    const { result, rerender } = renderHook((enabled: boolean) => useTables({ enabled }), { initialProps: false });
    expect(mocks.fetch).not.toHaveBeenCalled();
    expect(mocks.listeners.size).toBe(0);
    rerender(true);
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
    rerender(false);
    await act(async () => pending.resolve(page(['late'])));
    expect(result.current.tables).toEqual([]);
    expect(result.current.loading).toBe(false);
    expect(mocks.listeners.size).toBe(0);
  });

  it('ignores stale project responses and keeps shared lists separate', async () => {
    const pending = deferred<ListTablesResponse>();
    mocks.fetch.mockReturnValueOnce(pending.promise);
    const { result, rerender } = renderHook((opts: UseTablesOptions) => useTables(opts), {
      initialProps: { projectId: 'p1' } as UseTablesOptions,
    });
    mocks.fetch.mockResolvedValueOnce(page(['shared']));
    rerender({ shared: true, projectId: 'p2' });
    await waitFor(() => expect(result.current.tables[0]?.id).toBe('shared'));
    expect(mocks.fetch).toHaveBeenLastCalledWith({ shared: true, limit: 50 });
    await act(async () => pending.resolve(page(['old-project'])));
    expect(result.current.tables[0]?.id).toBe('shared');
  });

  it('coalesces table events, ignores other globals, and unsubscribes', async () => {
    mocks.fetch.mockResolvedValue(page(['a']));
    const { result, unmount } = renderHook(() => useTables());
    await waitFor(() => expect(result.current.loading).toBe(false));
    vi.useFakeTimers();
    await act(async () => {
      emit('doc_list_changed');
      await vi.advanceTimersByTimeAsync(TABLES_REFRESH_DEBOUNCE_MS);
    });
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
    await act(async () => {
      emit(); emit(); emit();
      await vi.advanceTimersByTimeAsync(TABLES_REFRESH_DEBOUNCE_MS);
    });
    expect(mocks.fetch).toHaveBeenCalledTimes(2);
    act(() => emit());
    unmount();
    await vi.advanceTimersByTimeAsync(TABLES_REFRESH_DEBOUNCE_MS);
    expect(mocks.fetch).toHaveBeenCalledTimes(2);
    expect(mocks.listeners.size).toBe(0);
  });

  it('refreshes a loaded window beyond the server page cap without truncating it', async () => {
    const ids = Array.from({ length: 200 }, (_, i) => `id-${i}`);
    mocks.fetch.mockResolvedValueOnce(page(ids, 'page-2'));
    const { result } = renderHook(() => useTables({ limit: 200 }));
    await waitFor(() => expect(result.current.tables).toHaveLength(200));
    mocks.fetch.mockResolvedValueOnce(page(['last']));
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.tables).toHaveLength(201));
    mocks.fetch.mockResolvedValueOnce(page(ids, 'new-page-2')).mockResolvedValueOnce(page(['last']));
    await act(async () => result.current.refresh());
    expect(result.current.tables).toHaveLength(201);
    expect(mocks.fetch).toHaveBeenLastCalledWith({ projectId: null, limit: 1, cursor: 'new-page-2' });
  });

  it('reports initial and next-page failures and permits retry', async () => {
    mocks.fetch.mockRejectedValueOnce(new Error('Offline'));
    const { result } = renderHook(() => useTables());
    await waitFor(() => expect(result.current.error).toBe('Offline'));
    mocks.fetch.mockResolvedValueOnce(page(['a'], 'next'));
    await act(async () => result.current.refresh());
    mocks.fetch.mockRejectedValueOnce(new Error('Page failed'));
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.error).toBe('Page failed'));
    expect(result.current.tables).toHaveLength(1);
    expect(result.current.hasMore).toBe(true);
    mocks.fetch.mockResolvedValueOnce(page(['b']));
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.tables).toHaveLength(2));
    expect(result.current.error).toBeNull();
  });

  it('clears cached content when access is revoked', async () => {
    mocks.fetch.mockResolvedValueOnce(page(['a']));
    const { result } = renderHook(() => useTables());
    await waitFor(() => expect(result.current.tables).toHaveLength(1));
    mocks.fetch.mockRejectedValueOnce(new ApiClientError('Tables disabled', 403, 'tables_disabled'));
    await act(async () => result.current.refresh());
    expect(result.current.tables).toEqual([]);
    expect(result.current.error).toBe('Tables disabled');
  });

  it('does not let an old page append after a replacing refresh', async () => {
    mocks.fetch.mockResolvedValueOnce(page(['a'], 'next'));
    const { result } = renderHook(() => useTables());
    await waitFor(() => expect(result.current.tables).toHaveLength(1));
    const pending = deferred<ListTablesResponse>();
    mocks.fetch.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(page(['fresh']));
    act(() => result.current.loadMore());
    await act(async () => result.current.refresh());
    await act(async () => pending.resolve(page(['old'])));
    expect(result.current.tables.map((t) => t.id)).toEqual(['fresh']);
    expect(result.current.loadingMore).toBe(false);
  });
});
