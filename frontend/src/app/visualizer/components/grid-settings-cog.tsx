'use client';

import { useState, useCallback, useEffect } from 'react';

export interface GridFilters {
  hideOldWpam: boolean;
  collapseTinyDocs: boolean;
  tinyDocThreshold: number;
  collapseToDocTypes: boolean;
}

interface GridSettingsCogProps {
  filters: GridFilters;
  onChange: (filters: GridFilters) => void;
}

export function GridSettingsCog({ filters, onChange }: GridSettingsCogProps) {
  const [open, setOpen] = useState(false);
  const [visible, setVisible] = useState(false);

  // Animate in
  useEffect(() => {
    if (open) {
      requestAnimationFrame(() => setVisible(true));
    }
  }, [open]);

  const handleClose = useCallback(() => {
    setVisible(false);
    setTimeout(() => setOpen(false), 150);
  }, []);

  const handleBackdropClick = useCallback((e: React.MouseEvent) => {
    if (e.target === e.currentTarget) handleClose();
  }, [handleClose]);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="cursor-pointer text-muted-foreground/50 hover:text-muted-foreground transition-colors"
        aria-label="Grid settings"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>
          <circle cx="12" cy="12" r="3"/>
        </svg>
      </button>

      {open && (
        <div
          className={`
            fixed inset-0 z-[100] flex items-center justify-center transition-all duration-150
            ${visible ? 'bg-black/50 backdrop-blur-sm' : 'bg-black/0'}
          `}
          onClick={handleBackdropClick}
        >
          <div
            className={`
              w-full max-w-sm bg-background border border-border/40 rounded-lg shadow-2xl
              transition-all duration-150
              ${visible ? 'opacity-100 scale-100' : 'opacity-0 scale-95'}
            `}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-border/30">
              <h2 className="text-sm font-medium text-foreground">Grid Settings</h2>
              <button
                onClick={handleClose}
                className="cursor-pointer text-muted-foreground/50 hover:text-foreground transition-colors"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>

            {/* Settings */}
            <div className="px-5 py-4 space-y-5">
              {/* Hide old WPAM */}
              <SettingRow
                title="Hide old WPAM editions"
                description="Only show the current (2026) WPAM edition"
                checked={filters.hideOldWpam}
                onToggle={() => onChange({ ...filters, hideOldWpam: !filters.hideOldWpam })}
              />

              {/* Collapse tiny docs */}
              <SettingRow
                title="Collapse tiny documents"
                description="Group documents with few chunks into an 'Other' bucket"
                checked={filters.collapseTinyDocs}
                onToggle={() => onChange({ ...filters, collapseTinyDocs: !filters.collapseTinyDocs })}
              />

              {/* Threshold input */}
              {filters.collapseTinyDocs && (
                <div className="pl-1">
                  <label className="flex items-center gap-3">
                    <span className="text-xs text-muted-foreground/70">Threshold</span>
                    <input
                      type="number"
                      min={1}
                      max={50}
                      value={filters.tinyDocThreshold}
                      onChange={(e) => {
                        const val = parseInt(e.target.value, 10);
                        if (!isNaN(val) && val >= 1) {
                          onChange({ ...filters, tinyDocThreshold: val });
                        }
                      }}
                      className="w-16 px-2 py-1 text-sm bg-foreground/[0.04] border border-border/30 rounded text-foreground text-center focus:outline-none focus:border-foreground/30"
                    />
                    <span className="text-xs text-muted-foreground/50">chunks or fewer</span>
                  </label>
                </div>
              )}

              {/* Collapse to doc types */}
              <SettingRow
                title="Collapse to document types"
                description="Show only authority-level groupings (Statutes, WPAM, etc.) instead of individual documents"
                checked={filters.collapseToDocTypes}
                onToggle={() => onChange({ ...filters, collapseToDocTypes: !filters.collapseToDocTypes })}
              />
            </div>

            {/* Footer */}
            <div className="px-5 py-3 border-t border-border/20 flex justify-end">
              <button
                onClick={handleClose}
                className="cursor-pointer text-xs font-medium text-foreground/70 px-3 py-1.5 rounded border border-border/30 hover:bg-foreground/[0.04] transition-colors"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function SettingRow({
  title,
  description,
  checked,
  onToggle,
}: {
  title: string;
  description: string;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <p className="text-sm text-foreground/90">{title}</p>
        <p className="text-xs text-muted-foreground/60 mt-0.5">{description}</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={onToggle}
        className={`
          cursor-pointer relative shrink-0 w-9 h-5 rounded-full transition-colors duration-200
          ${checked ? 'bg-foreground/70' : 'bg-foreground/20'}
        `}
      >
        <span
          className={`
            absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-background transition-transform duration-200
            ${checked ? 'translate-x-4' : 'translate-x-0'}
          `}
        />
      </button>
    </div>
  );
}
