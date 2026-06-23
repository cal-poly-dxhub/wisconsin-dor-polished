'use client';

import { useState, useRef, useEffect } from 'react';
import { useAuth } from '@/contexts/auth-context';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { ChevronLeft, MessageSquare, Moon, Sun, LogOut, Loader2, Plus, MoreHorizontal, Pencil, Trash2, Settings } from 'lucide-react';
import Image from 'next/image';
import { useTheme } from 'next-themes';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { useSessionsList, useDeleteSession, useRenameSession } from '@/hooks/api/chat';
import { getSessionHistory } from '@/api/chat-api';
import { useChatStore } from '@/stores/chat-store';
import { formatDistanceToNow } from 'date-fns';
import { SettingsModal } from '@/components/settings/settings-modal';

export function SessionsSidebar() {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [renameModal, setRenameModal] = useState<{ sessionId: string; title: string } | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [deleteModal, setDeleteModal] = useState<{ sessionId: string; title: string } | null>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const { signOut, session } = useAuth();
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const currentSessionId = useChatStore((state) => state.sessionId);

  const { data: sessionsData, isLoading, error, refetch } = useSessionsList();
  const clearHistory = useChatStore((state) => state.clearHistory);
  const reset = useChatStore((state) => state.reset);
  const setSessionId = useChatStore((state) => state.setSessionId);
  const addQuery = useChatStore((state) => state.addQuery);
  const stashSession = useChatStore((state) => state.stashSession);
  const restoreSession = useChatStore((state) => state.restoreSession);
  const setSwitchingSession = useChatStore((state) => state.setSwitchingSession);

  const deleteSessionMutation = useDeleteSession({
    onSuccess: (_data, deletedSessionId) => {
      toast.success('Session deleted');
      refetch();
      if (deletedSessionId === currentSessionId) {
        clearHistory();
        reset();
      }
    },
    onError: () => {
      toast.error('Failed to delete session');
    },
  });

  const renameSessionMutation = useRenameSession({
    onSuccess: () => {
      setRenameModal(null);
      refetch();
    },
    onError: () => {
      toast.error('Failed to rename session');
    },
  });

  useEffect(() => {
    if (renameModal && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renameModal]);

  const handleNewChat = () => {
    if (currentSessionId) {
      stashSession(currentSessionId);
    }
    clearHistory();
    reset();
    refetch();
    sessionStorage.setItem('explicit-new-chat', 'true');
  };

  const handleStartRename = (sessionId: string, currentTitle: string) => {
    setRenameValue(currentTitle || '');
    setTimeout(() => setRenameModal({ sessionId, title: currentTitle || '' }), 150);
  };

  const handleConfirmRename = () => {
    if (!renameModal) return;
    const trimmed = renameValue.trim();
    if (trimmed) {
      renameSessionMutation.mutate({ sessionId: renameModal.sessionId, title: trimmed });
    } else {
      setRenameModal(null);
    }
  };

  const handleDeleteSession = (sessionId: string, title: string) => {
    setTimeout(() => setDeleteModal({ sessionId, title: title || 'New chat' }), 150);
  };

  const handleConfirmDelete = () => {
    if (!deleteModal) return;
    deleteSessionMutation.mutate(deleteModal.sessionId);
    setDeleteModal(null);
  };

  const handleSessionSelect = async (sessionId: string) => {
    if (sessionId === currentSessionId) return;

    // Stash current session's in-memory state before switching
    if (currentSessionId) {
      stashSession(currentSessionId);
    }

    // Try restoring from cache first (preserves in-flight queries)
    const restored = restoreSession(sessionId);
    if (restored) {
      setSessionId(sessionId);
      // Ensure currentQueryId points to the last query for resource display
      const store = useChatStore.getState();
      const lastId = store.queryOrder[store.queryOrder.length - 1];
      if (lastId) {
        useChatStore.getState().setCurrentQueryId(lastId);
      }
      // If there's a pending query, check if the answer arrived while away
      const pendingId = store.queryOrder.find(
        id => store.queries[id]?.status === 'pending' || store.queries[id]?.status === 'streaming'
      );
      if (pendingId) {
        getSessionHistory(sessionId).then(history => {
          const msg = history.messages?.find(m => m.queryId === pendingId);
          if (msg?.answer && msg.answer.trim() !== '') {
            useChatStore.getState().updateQueryResponse(pendingId, msg.answer);
            useChatStore.getState().updateQueryStatus(pendingId, 'completed');
            useChatStore.getState().setChatState('idle');
          }
        }).catch(() => {});
      }
      return;
    }

    clearHistory();
    setSwitchingSession(true);

    try {
      const history = await getSessionHistory(sessionId);

      if (history.messages && history.messages.length > 0) {
        history.messages.forEach((msg) => {
          addQuery({
            queryId: msg.queryId,
            query: msg.query,
            type: 'outbound',
            timestamp: msg.timestamp || new Date().toISOString(),
            status: 'completed',
            response: {
              type: 'stream',
              content: msg.answer,
            },
            resources: msg.resources,
          });
        });
        const lastMsg = history.messages[history.messages.length - 1];
        useChatStore.getState().setCurrentQueryId(lastMsg.queryId);
      }

      setSessionId(sessionId);
    } catch (_error) {
      toast.error('Failed to load session');
    } finally {
      setSwitchingSession(false);
    }
  };

  const handleSignOut = async () => {
    try {
      await signOut();
      toast.success('Signed out successfully');
      router.push('/login');
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : 'Failed to sign out';
      toast.error(errorMessage);
    }
    setPopoverOpen(false);
  };

  const toggleTheme = () => {
    setTheme(theme === 'dark' ? 'light' : 'dark');
  };

  const email = session?.getIdToken().payload.email as string | undefined;
  const userInitial = email?.charAt(0).toUpperCase() || 'U';

  return (
    <div
      className="flex h-full flex-col border-r border-border bg-card transition-[width] duration-300 ease-in-out"
      style={{ width: isCollapsed ? '64px' : '256px' }}
      onMouseEnter={isCollapsed ? () => setIsHovered(true) : undefined}
      onMouseLeave={isCollapsed ? () => setIsHovered(false) : undefined}
    >
      {/* Header with Logo/Collapse Button */}
      <div className="flex items-center justify-center border-b border-border px-4 py-4"
        style={{ justifyContent: isCollapsed ? 'center' : 'space-between' }}
      >
        {isCollapsed ? (
          isHovered ? (
            <button
              onClick={() => setIsCollapsed(false)}
              className="rounded-md p-1.5 text-muted-foreground transition-[color,background-color,border-color] duration-200 hover:bg-muted hover:text-foreground cursor-pointer"
              aria-label="Expand sidebar"
            >
              <ChevronLeft
                className="h-4 w-4 transition-transform duration-300 ease-in-out"
                style={{ transform: 'rotate(180deg)' }}
              />
            </button>
          ) : (
            <Image
              src="/wisdor-logo.png"
              alt="WisDOR"
              width={28}
              height={28}
              className="rounded"
            />
          )
        ) : (
          <>
            <div className="flex items-center gap-2">
              <Image
                src="/wisdor-logo.png"
                alt="WisDOR"
                width={28}
                height={28}
                className="rounded shrink-0"
              />
              <div className="text-sm font-semibold whitespace-nowrap">WisDOR</div>
            </div>
            <button
              onClick={() => { setIsCollapsed(true); setIsHovered(false); }}
              className="rounded-md p-1.5 text-muted-foreground transition-[color,background-color,border-color] duration-200 hover:bg-muted hover:text-foreground cursor-pointer"
              aria-label="Collapse sidebar"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
          </>
        )}
      </div>

      {/* New Chat Button */}
      {!isCollapsed && (
        <div className="px-3 py-3 border-b border-border">
          <button
            onClick={handleNewChat}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 cursor-pointer"
          >
            <Plus className="h-4 w-4" />
            New Chat
          </button>
        </div>
      )}

      {/* Sessions List */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden">
        {isCollapsed ? (
          /* Collapsed Sessions Icon */
          <div className="flex flex-col items-center gap-3 py-4">
            <button
              onClick={handleNewChat}
              className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground cursor-pointer"
              aria-label="New chat"
            >
              <Plus className="h-5 w-5" />
            </button>
          </div>
        ) : (
          /* Expanded Sessions List */
          <div className="p-3">
            {/* "Recent" Subheader */}
            <div className="px-2 py-2">
              <h3 className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                Recent
              </h3>
            </div>
            <div className="space-y-1 mt-2">
              {isLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                </div>
              ) : error ? (
                <div className="text-xs text-destructive px-2 py-4 text-center">
                  Failed to load sessions
                </div>
              ) : sessionsData?.sessions && sessionsData.sessions.length > 0 ? (
                sessionsData.sessions.map((session) => (
                  <div
                    key={session.sessionId}
                    onClick={() => handleSessionSelect(session.sessionId)}
                    className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-muted group cursor-pointer ${
                      currentSessionId === session.sessionId
                        ? 'bg-muted text-foreground'
                        : 'text-muted-foreground'
                    }`}
                  >
                    <div className="flex flex-1 items-center gap-3 min-w-0">
                      <MessageSquare className="h-4 w-4 shrink-0" />
                      <div className="flex-1 overflow-hidden text-left">
                        <p className="truncate text-sm">
                          {session.title || 'New chat'}
                        </p>
                        {session.lastMessageAt && (
                          <p className="truncate text-xs text-muted-foreground">
                            {formatDistanceToNow(new Date(session.lastMessageAt), {
                              addSuffix: true,
                            })}
                          </p>
                        )}
                      </div>
                    </div>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <button
                          className="shrink-0 w-6 h-6 flex items-center justify-center opacity-0 group-hover:opacity-70 hover:!opacity-90 data-[state=open]:opacity-70 cursor-pointer"
                          aria-label="Session options"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <MoreHorizontal className="h-4.5 w-4.5" />
                        </button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent side="right" align="start" onClick={(e) => e.stopPropagation()}>
                        <DropdownMenuItem
                          onClick={() => handleStartRename(session.sessionId, session.title || '')}
                        >
                          <Pencil className="h-4 w-4" />
                          Rename
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() => handleDeleteSession(session.sessionId, session.title || '')}
                          className="text-destructive focus:text-destructive"
                        >
                          <Trash2 className="h-4 w-4" />
                          Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                ))
              ) : (
                <div className="text-xs text-muted-foreground px-2 py-8 text-center">
                  No recent chats
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Bottom User Profile */}
      <div className="border-t border-border pb-2">
        {isCollapsed ? (
          /* Collapsed: Just avatar icon */
          <div className="flex items-center justify-center py-4">
            <Popover open={popoverOpen} onOpenChange={setPopoverOpen}>
              <PopoverTrigger asChild>
                <button
                  className="flex h-10 w-10 items-center justify-center rounded-full bg-muted text-sm font-medium text-foreground transition-[color,background-color,border-color,box-shadow] duration-200 hover:bg-muted/80 hover:ring-2 hover:ring-muted-foreground/20 cursor-pointer"
                  aria-label="User menu"
                >
                  {userInitial}
                </button>
              </PopoverTrigger>
              <PopoverContent side="right" align="end" collisionPadding={16} className="w-56">
                <div className="space-y-1">
                  {/* Email header */}
                  <div className="px-2 py-2 border-b border-border">
                    <p className="text-xs text-muted-foreground truncate">
                      {email}
                    </p>
                  </div>

                  {/* Settings */}
                  <button
                    onClick={() => { setSettingsOpen(true); setPopoverOpen(false); }}
                    className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground transition-colors hover:bg-muted cursor-pointer"
                  >
                    <Settings className="h-4 w-4" />
                    <span>Settings</span>
                  </button>

                  {/* Theme Toggle */}
                  <button
                    onClick={toggleTheme}
                    className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground transition-colors hover:bg-muted cursor-pointer"
                  >
                    {theme === 'dark' ? (
                      <Sun className="h-4 w-4" />
                    ) : (
                      <Moon className="h-4 w-4" />
                    )}
                    <span>{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span>
                  </button>

                  {/* Sign out */}
                  <button
                    onClick={handleSignOut}
                    className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground transition-colors hover:bg-muted cursor-pointer"
                  >
                    <LogOut className="h-4 w-4" />
                    <span>Sign out</span>
                  </button>
                </div>
              </PopoverContent>
            </Popover>
          </div>
        ) : (
          /* Expanded: Full profile card */
          <Popover open={popoverOpen} onOpenChange={setPopoverOpen}>
            <PopoverTrigger asChild>
              <button
                className="flex w-full items-center gap-3 px-4 py-4 text-left transition-[color,background-color,border-color] duration-200 hover:bg-foreground/5 cursor-pointer"
                aria-label="User menu"
              >
                {/* Avatar */}
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-medium text-foreground">
                  {userInitial}
                </div>

                {/* Email */}
                <div className="flex-1 overflow-hidden">
                  <p className="truncate text-sm text-foreground text-left">
                    {email?.split('@')[0]}
                  </p>
                  <p className="truncate text-xs text-muted-foreground text-left">
                    {email}
                  </p>
                </div>
              </button>
            </PopoverTrigger>
            <PopoverContent side="right" align="end" collisionPadding={16} className="w-56">
              <div className="space-y-1">
                {/* Email header */}
                <div className="px-2 py-2 border-b border-border">
                  <p className="text-xs text-muted-foreground truncate">
                    {email}
                  </p>
                </div>

                {/* Settings */}
                <button
                  onClick={() => { setSettingsOpen(true); setPopoverOpen(false); }}
                  className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground transition-colors hover:bg-muted cursor-pointer"
                >
                  <Settings className="h-4 w-4" />
                  <span>Settings</span>
                </button>

                {/* Theme Toggle */}
                <button
                  onClick={toggleTheme}
                  className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground transition-colors hover:bg-muted cursor-pointer"
                >
                  {theme === 'dark' ? (
                    <Sun className="h-4 w-4" />
                  ) : (
                    <Moon className="h-4 w-4" />
                  )}
                  <span>{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span>
                </button>

                {/* Sign out */}
                <button
                  onClick={handleSignOut}
                  className="flex w-full items-center gap-3 rounded-md px-2 py-2 text-sm text-foreground transition-colors hover:bg-muted cursor-pointer"
                >
                  <LogOut className="h-4 w-4" />
                  <span>Sign out</span>
                </button>
              </div>
            </PopoverContent>
          </Popover>
        )}

        <SettingsModal open={settingsOpen} onOpenChange={setSettingsOpen} />

        {/* Rename Modal */}
        <Dialog open={!!renameModal} onOpenChange={(open) => { if (!open) setRenameModal(null); }}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Rename session</DialogTitle>
              <DialogDescription>Enter a new name for this session.</DialogDescription>
            </DialogHeader>
            <div className="mt-4 space-y-4">
              <input
                ref={renameInputRef}
                type="text"
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleConfirmRename();
                }}
                className="w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
              />
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setRenameModal(null)}
                  className="rounded-md px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted cursor-pointer"
                >
                  Cancel
                </button>
                <button
                  onClick={handleConfirmRename}
                  disabled={!renameValue.trim()}
                  className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50 cursor-pointer"
                >
                  Rename
                </button>
              </div>
            </div>
          </DialogContent>
        </Dialog>

        {/* Delete Confirmation Modal */}
        <Dialog open={!!deleteModal} onOpenChange={(open) => { if (!open) setDeleteModal(null); }}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete session</DialogTitle>
              <DialogDescription>
                Are you sure you want to delete &ldquo;{deleteModal?.title}&rdquo;? This action cannot be undone.
              </DialogDescription>
            </DialogHeader>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setDeleteModal(null)}
                className="rounded-md px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted cursor-pointer"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmDelete}
                className="rounded-md bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground transition-colors hover:bg-destructive/90 cursor-pointer"
              >
                Delete
              </button>
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
