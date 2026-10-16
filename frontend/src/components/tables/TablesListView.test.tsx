import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Project } from '../../api/types';
import { tableFixture, TABLE_ID } from '../../test/tableFixtures';
import { TablesView } from './TablesView';
import { TablesListView } from './TablesListView';

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  projects: [] as Project[],
  projectsLoaded: true,
}));
vi.mock('../../api/tablesApi', async (original) => ({
  ...await original<typeof import('../../api/tablesApi')>(), fetchTables: mocks.fetch,
}));
vi.mock('../../contexts/ProjectsContext', () => ({
  useProjects: () => ({ projects: mocks.projects, projectsLoaded: mocks.projectsLoaded }),
}));
vi.mock('../../services/PersistentWebSocket', () => ({
  persistentWebSocket: { onGlobalEvent: () => () => {} },
}));

const PROJECT: Project = {
  id: 'p1', name: 'Launch', user_id: 1, guide: '', public: false, archived: true,
  created_at: '2026-01-01T00:00:00', updated_at: null, conversation_count: 0,
};
const EMPTY = { tables: [], has_more: false, next_cursor: null };

describe('Tables list and gate', () => {
  beforeEach(() => {
    mocks.fetch.mockReset().mockResolvedValue(EMPTY);
    mocks.projects = [];
    mocks.projectsLoaded = true;
  });
  afterEach(cleanup);

  it('fetches nothing while gated, including on direct table links', () => {
    const { rerender } = render(
      <MemoryRouter><TablesView enabled={false} route={{ kind: 'list', projectId: null }} /></MemoryRouter>,
    );
    expect(screen.getByText('Quest Tables is turned off for your account.')).toBeTruthy();
    rerender(<MemoryRouter><TablesView enabled={false} route={{ kind: 'viewer', tableId: TABLE_ID }} /></MemoryRouter>);
    expect(mocks.fetch).not.toHaveBeenCalled();
  });

  it('groups own, shared, and archived-project tables with counts and working URLs', async () => {
    mocks.projects = [PROJECT];
    mocks.fetch.mockImplementation(async (opts) => ({
      ...EMPTY,
      tables: [tableFixture(opts.shared ? {
        id: 'shared', title: 'Shared contacts', shared_with_me: true, permission: 'read',
        owner: { id: 2, name: 'Ada', email: 'ada@example.com' },
      } : opts.projectId ? {
        id: 'project', title: 'Launch tasks', scope: 'project', project_id: 'p1', mode: 'public',
      } : {})],
    }));
    render(<MemoryRouter><TablesListView projectId={null} /></MemoryRouter>);
    const own = await screen.findByRole('table', { name: 'Your tables' });
    expect(within(own).getByRole('link', { name: 'Contacts' }).getAttribute('href')).toBe(`/tables/${TABLE_ID}`);
    expect(within(own).getByText('12')).toBeTruthy();
    expect(within(own).getByText('3')).toBeTruthy();
    expect(await screen.findByText('Shared by Ada')).toBeTruthy();
    expect(await screen.findByText('Launch tasks')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Launch' }).getAttribute('href')).toBe('/tables?project=p1');
    expect(screen.getByText('Archived')).toBeTruthy();
    expect(screen.getAllByRole('columnheader', { name: 'Mode' })).toHaveLength(3);
  });

  it('filters loaded rows by title, description and shared owner without hiding load more', async () => {
    mocks.fetch.mockImplementation(async (opts) => ({
      tables: opts.shared ? [tableFixture({
        id: 'shared', title: 'Team', description: '', shared_with_me: true,
        owner: { id: 2, name: 'Ada', email: 'ada@example.com' },
      })] : [tableFixture()],
      has_more: !opts.shared, next_cursor: opts.shared ? null : 'next',
    }));
    render(<MemoryRouter><TablesListView projectId={null} /></MemoryRouter>);
    await screen.findByRole('link', { name: 'Contacts' });
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'ADA@' } });
    expect(screen.queryByRole('link', { name: 'Contacts' })).toBeNull();
    expect(screen.getByRole('link', { name: 'Team' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Load more: Your tables' })).toBeTruthy();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'FOLLOW UP' } });
    expect(screen.getByRole('link', { name: 'Contacts' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Team' })).toBeNull();
  });

  it('only fetches the selected project and shows errors rather than a false empty state', async () => {
    mocks.projects = [PROJECT];
    mocks.fetch.mockRejectedValue(new Error('Project not found'));
    render(<MemoryRouter><TablesListView projectId="missing" /></MemoryRouter>);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByText('Project not found')).toBeTruthy();
    expect(mocks.fetch).toHaveBeenCalledTimes(1);
    expect(mocks.fetch).toHaveBeenCalledWith({ projectId: 'missing', limit: 50 });
    expect(screen.queryByText(/No tables yet/)).toBeNull();
    mocks.fetch.mockResolvedValue(EMPTY);
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('No tables yet. Ask Quest to create one.')).toBeTruthy();
  });

  it('pages project groups beyond their first page', async () => {
    mocks.projects = [PROJECT];
    mocks.fetch.mockImplementation(async (opts) => opts.projectId ? ({
      tables: [tableFixture({ id: opts.cursor ? 'second' : 'first', title: opts.cursor ? 'Second page' : 'First page' })],
      has_more: !opts.cursor, next_cursor: opts.cursor ? null : 'project-next',
    }) : EMPTY);
    render(<MemoryRouter><TablesListView projectId={null} /></MemoryRouter>);
    await screen.findByText('First page');
    fireEvent.click(screen.getByRole('button', { name: 'Load more: Launch' }));
    expect(await screen.findByText('Second page')).toBeTruthy();
    expect(mocks.fetch).toHaveBeenCalledWith({ projectId: 'p1', limit: 50, cursor: 'project-next' });
  });

  it('waits for projects to load before mounting project streams', async () => {
    mocks.projectsLoaded = false;
    mocks.projects = [PROJECT];
    const { rerender } = render(<MemoryRouter><TablesListView projectId={null} /></MemoryRouter>);
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledTimes(2));
    expect(screen.getByText('Loading project tables...')).toBeTruthy();
    mocks.projectsLoaded = true;
    rerender(<MemoryRouter><TablesListView projectId={null} /></MemoryRouter>);
    await waitFor(() => expect(mocks.fetch).toHaveBeenCalledWith({ projectId: 'p1', limit: 50 }));
  });

  it('provides a viewer placeholder and handles invalid links without API calls', async () => {
    const { rerender } = render(
      <MemoryRouter><TablesView enabled route={{ kind: 'viewer', tableId: TABLE_ID }} /></MemoryRouter>,
    );
    expect(screen.getByRole('heading', { name: 'Table viewer' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Back to all tables' }).getAttribute('href')).toBe('/tables');
    await act(async () => {
      rerender(<MemoryRouter><TablesView enabled route={null} /></MemoryRouter>);
    });
    expect(screen.getByText('Invalid table link')).toBeTruthy();
    expect(mocks.fetch).not.toHaveBeenCalled();
  });
});
