import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  fetchTables, TABLES_MAX_PAGE_SIZE,
  type FetchTablesOptions, type QuestTable,
} from '../api/tablesApi';
import { persistentWebSocket } from '../services/PersistentWebSocket';
import { ApiClientError } from '../api/request';

export const TABLES_REFRESH_DEBOUNCE_MS = 300;

export interface UseTablesOptions {
  projectId?: string | null;
  shared?: boolean;
  limit?: number;
  enabled?: boolean;
}

export interface TablesList {
  tables: QuestTable[];
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  hasMore: boolean;
  loadMore: () => void;
  refresh: () => Promise<void>;
}

interface ListState {
  key: string | null;
  tables: QuestTable[];
  cursor: string | null;
  error: string | null;
}
const EMPTY: ListState = { key: null, tables: [], cursor: null, error: null };
function accessRevoked(err: unknown): boolean {
  return err instanceof ApiClientError
    && (err.statusCode === 401 || err.statusCode === 403 || err.statusCode === 404);
}

/** Each stream owns its cursor; old requests cannot land on a new stream. */
export function useTables({
  projectId = null, shared = false, limit = 50, enabled = true,
}: UseTablesOptions = {}): TablesList {
  const pageSize = Number.isFinite(limit)
    ? Math.min(TABLES_MAX_PAGE_SIZE, Math.max(1, Math.floor(limit))) : 50;
  const project = shared ? null : projectId || null;
  const key = enabled ? `${shared ? 'shared' : `project:${project ?? ''}`}|${pageSize}` : null;
  const options = useMemo<FetchTablesOptions>(
    () => shared ? { shared: true } : { projectId: project }, [shared, project],
  );
  const [state, setState] = useState<ListState>(EMPTY);
  const stateRef = useRef(state);
  const generation = useRef(0);
  const paging = useRef(false);
  const replacing = useRef(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const commit = useCallback((next: ListState) => {
    stateRef.current = next;
    setState(next);
  }, []);

  const fetchWindow = useCallback(async (count: number) => {
    if (key === null) return;
    const seq = ++generation.current;
    replacing.current = true;
    paging.current = false;
    setLoadingMore(false);
    try {
      // A loaded window can exceed the server's per-page cap. Re-page it
      // rather than silently dropping rows or losing its continuation cursor.
      const tables: QuestTable[] = [];
      const seen = new Set<string>();
      let cursor: string | null = null;
      let remaining = count;
      do {
        const requestLimit = Math.min(TABLES_MAX_PAGE_SIZE, remaining);
        const response = await fetchTables({
          ...options, limit: requestLimit,
          ...(cursor ? { cursor } : {}),
        });
        if (seq !== generation.current) return;
        for (const table of response.tables) {
          if (!seen.has(table.id)) { seen.add(table.id); tables.push(table); }
        }
        const next = response.has_more ? response.next_cursor : null;
        if (next === cursor && next !== null) throw new Error('Invalid table pagination');
        cursor = next;
        remaining -= requestLimit;
      } while (cursor && remaining > 0);
      commit({ key, tables, cursor, error: null });
    } catch (err) {
      if (seq !== generation.current) return;
      // Preserve already rendered rows on transient failures, but never
      // retain data when the server says access or the feature was revoked.
      const previous = !accessRevoked(err) && stateRef.current.key === key ? stateRef.current : EMPTY;
      commit({
        key, tables: previous.tables, cursor: previous.cursor,
        error: err instanceof Error ? err.message : 'Failed to load tables',
      });
    } finally {
      if (seq === generation.current) replacing.current = false;
    }
  }, [key, options, commit]);

  useEffect(() => {
    generation.current += 1;
    paging.current = false;
    replacing.current = false;
    setLoadingMore(false);
    commit(EMPTY);
    void fetchWindow(pageSize);
    return () => { generation.current += 1; };
  }, [fetchWindow, pageSize, commit]);

  const refresh = useCallback(async () => {
    const count = stateRef.current.key === key ? stateRef.current.tables.length : 0;
    await fetchWindow(Math.max(pageSize, count));
  }, [key, pageSize, fetchWindow]);

  const loadMore = useCallback(() => {
    const previous = stateRef.current;
    if (key === null || previous.key !== key || !previous.cursor || paging.current || replacing.current) return;
    const seq = generation.current;
    const cursor = previous.cursor;
    paging.current = true;
    setLoadingMore(true);
    void (async () => {
      try {
        const response = await fetchTables({ ...options, limit: pageSize, cursor });
        if (seq !== generation.current || stateRef.current.cursor !== cursor) return;
        const seen = new Set(previous.tables.map((table) => table.id));
        commit({
          key, tables: [...previous.tables, ...response.tables.filter((table) => {
            if (seen.has(table.id)) return false;
            seen.add(table.id);
            return true;
          })],
          cursor: response.has_more ? response.next_cursor : null, error: null,
        });
      } catch (err) {
        if (seq !== generation.current) return;
        commit({
          ...(accessRevoked(err) ? { ...EMPTY, key } : previous),
          error: err instanceof Error ? err.message : 'Failed to load more tables',
        });
      } finally {
        if (seq === generation.current) {
          paging.current = false;
          setLoadingMore(false);
        }
      }
    })();
  }, [key, options, pageSize, commit]);

  useEffect(() => {
    if (key === null) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const unsubscribe = persistentWebSocket.onGlobalEvent((event) => {
      if (event.type !== 'table_list_changed') return;
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(() => { void refresh(); }, TABLES_REFRESH_DEBOUNCE_MS);
    });
    return () => {
      if (timer !== null) clearTimeout(timer);
      unsubscribe();
    };
  }, [key, refresh]);

  const current = key !== null && state.key === key ? state : EMPTY;
  return {
    tables: current.tables, loading: key !== null && state.key !== key,
    loadingMore: key !== null && loadingMore, error: current.error,
    hasMore: current.cursor !== null, loadMore, refresh,
  };
}
