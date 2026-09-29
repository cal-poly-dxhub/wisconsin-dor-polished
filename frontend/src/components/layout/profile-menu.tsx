'use client';

import { LogOut, Monitor, Moon, Settings, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';

const THEMES = [
  { value: 'light', label: 'Light', Icon: Sun },
  { value: 'dark', label: 'Dark', Icon: Moon },
  { value: 'system', label: 'System', Icon: Monitor },
] as const;

const ITEM =
  'flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-sm text-foreground transition-colors hover:bg-muted focus-visible:bg-muted focus-visible:outline-none cursor-pointer';

interface ProfileMenuProps {
  email?: string;
  isAdmin?: boolean;
  onOpenSettings: () => void;
  onSignOut: () => void;
}

/** Body of the sidebar's account popover, shared by the collapsed and
 *  expanded sidebar so the two cannot drift. */
export function ProfileMenu({ email, isAdmin, onOpenSettings, onSignOut }: ProfileMenuProps) {
  const { theme, setTheme } = useTheme();
  const name = email?.split('@')[0];
  const initial = email?.charAt(0).toUpperCase() || 'U';

  return (
    <div className="text-sm">
      <div className="flex items-center gap-3 px-2 pt-1 pb-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-medium text-foreground">
          {initial}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate font-medium text-foreground">{name}</p>
            {isAdmin && (
              <span className="shrink-0 rounded bg-muted px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                Admin
              </span>
            )}
          </div>
          <p className="truncate text-xs text-muted-foreground">{email}</p>
        </div>
      </div>

      <div className="border-t border-border pt-1.5">
        <button onClick={onOpenSettings} className={ITEM}>
          <Settings className="h-4 w-4 text-muted-foreground" />
          <span>Settings</span>
        </button>

        <div className="flex items-center justify-between gap-2 px-2 py-1.5">
          <span className="text-foreground">Theme</span>
          <div role="radiogroup" aria-label="Theme" className="flex rounded-md bg-muted p-0.5">
            {THEMES.map(({ value, label, Icon }) => {
              const active = theme === value;
              return (
                <button
                  key={value}
                  role="radio"
                  aria-checked={active}
                  aria-label={label}
                  title={label}
                  onClick={() => setTheme(value)}
                  className={`flex h-6 w-7 items-center justify-center rounded transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring ${
                    active
                      ? 'bg-background text-foreground shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" />
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="mt-1.5 border-t border-border pt-1.5">
        <button onClick={onSignOut} className={ITEM}>
          <LogOut className="h-4 w-4 text-muted-foreground" />
          <span>Sign out</span>
        </button>
      </div>
    </div>
  );
}
