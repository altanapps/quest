import { API_BASE_URL } from './config';
import { apiGet } from './request';

export const TABLES_FEATURE = 'tables';
export const TABLES_MAX_PAGE_SIZE = 200;
export const SIDEBAR_TABLE_LIMIT = 5;

export interface TableUser {
  id: number;
  name: string | null;
  email: string;
}

/** List-row contract from chat/tables/routes.py; schema is detail-only. */
export interface QuestTable {
  id: string;
  owner_id: number;
  project_id: string | null;
  title: string;
  description: string;
  mode: 'private' | 'public';
  scope: 'user' | 'project';
  schema_version: number;
  row_count: number;
  column_count: number;
  require_approval: boolean;
  last_write_source: string | null;
  created_at: string;
  updated_at: string;
  shared: boolean;
  shared_with_me: boolean;
  permission: 'read' | 'write' | null;
  owner: TableUser | null;
  last_write_user: TableUser | null;
  shares: {
    id: number;
    table_id: string;
    user_id: number | null;
    permission: 'read' | 'write';
    created_at: string;
    user: TableUser | null;
  }[] | null;
  access: {
    can_read: boolean;
    rows: 'free' | 'approval' | 'denied';
    schema: 'free' | 'approval' | 'denied';
    can_edit: boolean;
    can_restore: boolean;
    can_rename: boolean;
    can_delete: boolean;
    can_share: boolean;
    can_require_approval: boolean;
    can_edit_schema: boolean;
    can_download_history: boolean;
    deny_reason: string | null;
    schema_deny_reason: string | null;
  };
}

export interface ListTablesResponse {
  tables: QuestTable[];
  has_more: boolean;
  next_cursor: string | null;
}

export interface FetchTablesOptions {
  projectId?: string | null;
  shared?: boolean;
  limit?: number;
  cursor?: string | null;
}

export function fetchTables(opts: FetchTablesOptions = {}): Promise<ListTablesResponse> {
  return apiGet(`${API_BASE_URL}/tables`, {
    query: {
      project_id: opts.projectId || undefined,
      shared: opts.shared ? true : undefined,
      limit: opts.limit,
      cursor: opts.cursor || undefined,
    },
  });
}
