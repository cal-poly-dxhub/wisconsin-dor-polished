'use client';
import { useChatStore } from '@/stores/chat-store';
import { KeyboardEvent, useEffect, useLayoutEffect, useRef, useState } from 'react';

export interface ChatInputProps {
  onSendMessage: (message: string) => void;
  placeholder?: string;
  className?: string;
}

export function ChatInput({
  onSendMessage,
  placeholder = 'Type your message...',
  className = '',
}: ChatInputProps) {
  // Seed from the store draft once on mount so a programmatic prefill (e.g.
  // "Start new chat" on a topic-shift suggestion carries the question over)
  // lands in a freshly-mounted input. Consumed immediately so it doesn't
  // resurrect on later mounts.
  const [message, setMessage] = useState(
    () => useChatStore.getState().draftMessage
  );

  const chatState = useChatStore(s => s.chatState);
  const setChatState = useChatStore(s => s.setChatState);
  const setDraftMessage = useChatStore(s => s.setDraftMessage);
  const disabled = chatState !== 'idle';
  const textAreaRef = useRef<HTMLTextAreaElement>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (useChatStore.getState().draftMessage) {
      setDraftMessage('');
    }
    // Run once on mount; the initial value was already captured above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useLayoutEffect(() => {
    const ta = textAreaRef.current;
    if (!ta) return;
    ta.style.transition = 'none';
    ta.style.height = '0px';
    const scrollHeight = ta.scrollHeight;
    const maxHeight = 200;
    setExpanded(scrollHeight > 36);
    ta.style.height = Math.min(scrollHeight, maxHeight) + 'px';
    ta.style.overflowY = scrollHeight > maxHeight ? 'auto' : 'hidden';
    void ta.offsetHeight;
    ta.style.transition = 'height 0.2s ease';
  }, [message]);

  const handleSend = () => {
    if (message.trim() && !disabled) {
      setChatState('sending');
      onSendMessage(message.trim());
      setMessage('');
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className={`flex items-center justify-center px-4 pb-6 pt-4 pointer-events-none ${className}`}>
      <div
        className="pointer-events-auto grid w-full max-w-2xl rounded-[26px] border border-black/15 bg-white/90 p-[10px] shadow-[0_4px_24px_rgba(0,0,0,0.08)] backdrop-blur-xl dark:border-border/50 dark:bg-[hsl(0_0%_12%/0.85)] dark:shadow-[0_8px_40px_rgba(0,0,0,0.7)]"
        style={{
          gridTemplateAreas: expanded
            ? "'primary primary' 'footer trailing'"
            : "'primary trailing'",
          gridTemplateColumns: '1fr auto',
          gridTemplateRows: expanded ? 'auto auto' : 'auto',
        }}
      >
        <textarea
          ref={textAreaRef}
          value={message}
          onChange={e => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? 'Please wait...' : placeholder}
          disabled={disabled}
          rows={1}
          className="resize-none self-center bg-transparent pl-2 pr-2 text-foreground placeholder:text-muted-foreground/70 outline-none disabled:cursor-not-allowed disabled:opacity-50"
          style={{
            gridArea: 'primary',
            fontSize: 'clamp(0.9rem, 1vw + 0.5rem, 1.05rem)',
            transition: 'height 0.2s ease',
          }}
        />
        <button
          onClick={handleSend}
          disabled={!message.trim() || disabled}
          className={`self-end shrink-0 rounded-full bg-foreground p-2 transition-opacity duration-200
            ${disabled
              ? 'opacity-30 cursor-not-allowed'
              : message.trim()
                ? 'opacity-100 hover:opacity-80 cursor-pointer'
                : 'opacity-30 cursor-pointer'
            }`}
          style={{ gridArea: 'trailing' }}
          aria-label="Send"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M8 12V4M8 4L4.5 7.5M8 4L11.5 7.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-background"/>
          </svg>
        </button>
      </div>
    </div>
  );
}
