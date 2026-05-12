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
  const [isMultiline, setIsMultiline] = useState(false);

  useLayoutEffect(() => {
    const ta = textAreaRef.current;
    if (!ta) return;
    ta.style.minHeight = '0';
    ta.style.height = 'auto';
    const natural = ta.scrollHeight;
    const multi = natural > 40;
    setIsMultiline(multi);
    const height = Math.min(Math.max(natural, multi ? 72 : 24), 200);
    ta.style.height = height + 'px';
    ta.style.minHeight = '';
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
      <div
        className={`flex w-full max-w-4xl border border-border bg-background shadow-sm transition-[border-radius] duration-150 ${
          isMultiline ? 'flex-col rounded-2xl p-3' : 'items-center rounded-full pl-4 pr-2 py-2'
        }`}
      >
        <textarea
          ref={textAreaRef}
          value={message}
          onChange={e => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? 'Please wait...' : placeholder}
          disabled={disabled}
          rows={1}
          className={`flex-1 resize-none bg-transparent text-foreground placeholder:text-muted-foreground/60 outline-none disabled:cursor-not-allowed disabled:opacity-50 ${
            isMultiline ? 'px-1' : 'pl-1'
          }`}
          style={{ maxHeight: '200px', fontSize: 'clamp(0.9rem, 1vw + 0.5rem, 1.05rem)' }}
        />
        <div className={`flex items-center ${isMultiline ? 'justify-end pt-2' : ''}`}>
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
    </div>
  );
}
