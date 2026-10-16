import type { ReactNode } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Link, MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { tableFixture, TABLE_ID } from './test/tableFixtures';

const mocks = vi.hoisted(() => ({
  mobile: false,
  enabled: true,
  fetch: vi.fn(),
  setConversation: vi.fn(),
  setProject: vi.fn(),
  setRequests: vi.fn(),
}));
vi.mock('./contexts/AppProviders', () => ({
  AppProviders: ({ children }: { children: ReactNode }) => children,
}));
vi.mock('./contexts/AuthContext', () => ({
  useAuth: () => ({
    isAuthenticated: true, isCheckingAuth: false, hasAnyServiceConnected: true,
    enabledFeatures: mocks.enabled ? ['tables'] : [],
  }),
}));
vi.mock('./contexts/AppConfigContext', () => ({ useAppConfig: () => ({ appName: 'Quest' }) }));
vi.mock('./contexts/NavigationContext', () => ({
  useNavigationState: () => ({
    activeConversationId: null, setActiveConversationId: mocks.setConversation,
    isSettingsOpen: false, setSettingsOpen: vi.fn(),
    showRequestsView: false, setShowRequestsView: mocks.setRequests,
  }),
}));
vi.mock('./contexts/ProjectsContext', () => ({
  useProjects: () => ({
    projects: [], projectsLoaded: true, drilledProjectId: null, setActiveProjectId: mocks.setProject,
  }),
}));
vi.mock('./hooks/useIsMobile', () => ({ useIsMobile: () => mocks.mobile }));
vi.mock('./api/tablesApi', async (original) => ({
  ...await original<typeof import('./api/tablesApi')>(), fetchTables: mocks.fetch,
}));
vi.mock('./services/PersistentWebSocket', () => ({
  persistentWebSocket: { onGlobalEvent: () => () => {} },
}));
vi.mock('./components/Sidebar', () => ({
  Sidebar: () => <nav><Link to="/tables">Browse tables</Link></nav>,
}));
vi.mock('./components/ChatPanel', () => ({ ChatPanel: () => <div>Chat panel</div> }));
vi.mock('./components/HomeComposer', () => ({ HomeComposer: () => <div>Home composer</div> }));
vi.mock('./components/RightPanel', () => ({ RightPanel: () => <aside>Workspace</aside> }));
vi.mock('./components/SettingsModal', () => ({ SettingsModal: () => null }));
vi.mock('./components/SignInScreen', () => ({ SignInScreen: () => null }));
vi.mock('./components/SetPasswordScreen', () => ({ SetPasswordScreen: () => null }));
vi.mock('./components/RequestsView', () => ({ RequestsView: () => null }));
vi.mock('./components/docs/DocsListView', () => ({ DocsListView: () => null }));
vi.mock('./components/docs/DocViewer', () => ({ DocViewer: () => null }));

describe('Tables app navigation', () => {
  beforeEach(() => {
    mocks.mobile = false;
    mocks.enabled = true;
    mocks.setConversation.mockClear();
    mocks.fetch.mockReset().mockImplementation(async (opts) => ({
      tables: opts.shared ? [] : [tableFixture()],
      has_more: false, next_cursor: null,
    }));
  });
  afterEach(cleanup);

  it('opens the desktop list from navigation and supports browser-link table routes', async () => {
    render(<MemoryRouter initialEntries={['/']}><App /></MemoryRouter>);
    fireEvent.click(screen.getByRole('link', { name: 'Browse tables' }));
    expect(await screen.findByRole('heading', { name: 'All Tables' })).toBeTruthy();
    expect(screen.queryByText('Workspace')).toBeNull();
    expect(screen.queryByText('Home composer')).toBeNull();
    fireEvent.click(await screen.findByRole('link', { name: 'Contacts' }));
    expect(screen.getByRole('heading', { name: 'Table viewer' })).toBeTruthy();
    fireEvent.click(screen.getByRole('link', { name: 'Back to all tables' }));
    expect(await screen.findByRole('heading', { name: 'All Tables' })).toBeTruthy();
  });

  it('opens a filtered project list directly without treating its query as a conversation', async () => {
    render(<MemoryRouter initialEntries={['/tables?project=p1']}><App /></MemoryRouter>);
    expect(await screen.findByRole('link', { name: 'Contacts' })).toBeTruthy();
    expect(mocks.fetch).toHaveBeenCalledWith({ projectId: 'p1', limit: 50 });
    expect(mocks.setConversation).toHaveBeenCalledWith(null);
  });

  it('does not fetch for gated direct links or malformed table addresses', () => {
    mocks.enabled = false;
    const { unmount } = render(
      <MemoryRouter initialEntries={[`/tables/${TABLE_ID}`]}><App /></MemoryRouter>,
    );
    expect(screen.getByText('Quest Tables is turned off for your account.')).toBeTruthy();
    expect(mocks.fetch).not.toHaveBeenCalled();
    unmount();
    mocks.enabled = true;
    render(<MemoryRouter initialEntries={['/tables/not-valid']}><App /></MemoryRouter>);
    expect(screen.getByRole('heading', { name: 'Invalid table link' })).toBeTruthy();
    expect(mocks.fetch).not.toHaveBeenCalled();
  });

  it('closes the phone drawer on Tables navigation and hides the workspace button', async () => {
    mocks.mobile = true;
    const { container } = render(<MemoryRouter initialEntries={['/']}><App /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: 'Open menu' }));
    expect(container.querySelector('.mobile-drawer.open')).not.toBeNull();
    fireEvent.click(screen.getByRole('link', { name: 'Browse tables' }));
    expect(await screen.findByRole('heading', { name: 'All Tables' })).toBeTruthy();
    expect(container.querySelector('.mobile-drawer.open')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Open workspace' })).toBeNull();
  });
});
