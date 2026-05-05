'use client';
import { useChatStore } from '@/stores/chat-store';
import { KeyboardEvent, useLayoutEffect, useRef, useState } from 'react';

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
  const [message, setMessage] = useState('');

  const chatState = useChatStore(s => s.chatState);
  const setChatState = useChatStore(s => s.setChatState);
  const disabled = chatState !== 'idle';
  const textAreaRef = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const ta = textAreaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
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
    <div className={`flex items-center justify-center px-4 py-4 ${className}`}>
      <div className="flex w-full max-w-4xl items-center gap-2 rounded-full border border-border bg-background pl-4 pr-2 py-2 shadow-sm">
        <textarea
          ref={textAreaRef}
          value={message}
          onChange={e => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? 'Please wait...' : placeholder}
          disabled={disabled}
          rows={1}
          className="flex-1 resize-none bg-transparent text-foreground placeholder:text-muted-foreground/60 outline-none disabled:cursor-not-allowed disabled:opacity-50 pl-1"
          style={{ minHeight: '1.5rem', maxHeight: '120px', fontSize: 'clamp(0.9rem, 1vw + 0.5rem, 1.05rem)' }}
        />
        <button
          onClick={handleSend}
          disabled={!message.trim() || disabled}
          className={`shrink-0 rounded-full bg-foreground p-2 transition-all duration-200
            ${disabled
              ? 'opacity-30 cursor-not-allowed'
              : message.trim()
                ? 'opacity-100 hover:opacity-80 cursor-pointer'
                : 'opacity-30 cursor-pointer'
            }`}
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
