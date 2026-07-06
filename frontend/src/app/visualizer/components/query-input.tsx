'use client';

import { useState, useCallback, KeyboardEvent } from 'react';

interface QueryInputProps {
  onSubmit: (query: string) => void;
  disabled?: boolean;
  embedded?: boolean;
}

export function QueryInput({ onSubmit, disabled, embedded }: QueryInputProps) {
  const [value, setValue] = useState('');

  const handleSubmit = useCallback(() => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
    setValue('');
  }, [value, disabled, onSubmit]);

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }, [handleSubmit]);

  return (
    <input
      type="text"
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={handleKeyDown}
      disabled={disabled}
      placeholder="Enter a query..."
      className={
        embedded
          ? 'w-full min-w-0 bg-transparent text-sm text-foreground placeholder:text-muted-foreground/40 outline-none px-3 py-2 transition-colors disabled:opacity-40'
          : 'w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground/40 border-b border-border/30 focus:border-foreground/20 outline-none pb-2 transition-colors disabled:opacity-40'
      }
    />
  );
}
