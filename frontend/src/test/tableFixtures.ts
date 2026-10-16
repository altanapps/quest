import type { QuestTable } from '../api/tablesApi';

export const TABLE_ID = 'a14a7250-d37a-45f2-9a8a-c3f6fd87c734';

export function tableFixture(overrides: Partial<QuestTable> = {}): QuestTable {
  return {
    id: TABLE_ID, owner_id: 1, project_id: null,
    title: 'Contacts', description: 'People to follow up with',
    mode: 'private', scope: 'user', schema_version: 1, row_count: 12, column_count: 3,
    require_approval: false, last_write_source: 'ui:1',
    created_at: '2026-10-01T00:00:00', updated_at: '2026-10-01T00:00:00',
    shared: false, shared_with_me: false, permission: null,
    owner: null, last_write_user: null, shares: [],
    access: {
      can_read: true, rows: 'free', schema: 'free', can_edit: true,
      can_restore: true, can_rename: true, can_delete: true, can_share: true,
      can_require_approval: true, can_edit_schema: true, can_download_history: true,
      deny_reason: null, schema_deny_reason: null,
    },
    ...overrides,
  };
}
