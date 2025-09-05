'use client';
import { Button } from '@/components/ui/button';
import { useChatStore } from '@/stores/chat-store';
import { Send } from 'lucide-react';
import { KeyboardEvent, useLayoutEffect, useRef, useState } from 'react';

export interface ChatInputProps {
  onSendMessage: (message: string) => void;
  placeholder?: string;
  className?: string;
}

export function ChatInput({
  onSendMessage,
  placeholder = 'Ask me anything.',
  className = '',
}: ChatInputProps) {
  const [message, setMessage] = useState('');
  const [isExpanded, setIsExpanded] = useState(false);

  // Enable chat input iff state is idle
  const chatState = useChatStore(s => s.chatState);
  const setChatState = useChatStore(s => s.setChatState);
  const disabled = chatState !== 'idle';
  const textAreaRef = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    // Conforms text input to content size
    const ta = textAreaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    const newHeight = Math.min(ta.scrollHeight, 120);
    ta.style.height = newHeight + 'px';
    setIsExpanded(newHeight > 24);
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

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setMessage(e.target.value);
  };

  return (
    <div className={`relative ${className}`}>
      {/* Frosted glass input container */}
      <div
        className={`relative flex items-center gap-3 rounded-lg border px-4 pr-2 backdrop-blur-[16px] transition-all duration-200 ${isExpanded ? 'py-4' : 'py-2'
          } ${disabled
            ? 'bg-gray-100/5 dark:bg-white/2'
            : 'border-1 border-border/800 bg-white/10 dark:border-border/200 dark:bg-white/5'
          }`}
      >
        {/* Grain overlay */}
        <div className="pointer-events-none absolute inset-0 rounded-lg">
          <div
            className={`grain-overlay transition-opacity duration-200 ${disabled ? 'opacity-30' : 'opacity-100'}`}
          ></div>
        </div>

        {/* Disabled overlay */}
        {disabled && (
          <div className="absolute inset-0 rounded-lg bg-gray-500/10 backdrop-blur-[2px] dark:bg-gray-800/20"></div>
        )}

        <textarea
          value={message}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          placeholder={disabled ? 'Please wait...' : placeholder}
          disabled={disabled}
          rows={1}
          ref={textAreaRef}
          className={`flex-1 resize-none overflow-y-auto border-0 bg-transparent text-xl font-light shadow-none transition-all duration-200 outline-none focus-visible:ring-0 focus-visible:ring-offset-0 ${disabled
            ? 'cursor-not-allowed text-gray-400 placeholder:text-gray-400/60 dark:text-gray-600 dark:placeholder:text-gray-600/60'
            : 'text-gray-800 placeholder:text-gray-600 dark:text-white dark:placeholder:text-gray-500'
            }`}
          style={{
            minHeight: '1.5rem',
            maxHeight: '120px',
          }}
        />
        <Button
          onClick={handleSend}
          disabled={!message.trim() || disabled}
          size="icon"
          className={`transition-all duration-200 mr-1 ${disabled
            ? 'cursor-not-allowed border-gray-300/10 bg-gray-100/20 text-gray-400 hover:bg-gray-100/20 dark:border-white/5 dark:bg-white/5 dark:text-gray-600 dark:hover:bg-white/5'
            : 'border-gray-300/30 bg-neutral-800 text-gray-700 backdrop-blur-sm hover:bg-gray-800/60 dark:border-white/20 dark:bg-white/10 dark:text-white dark:hover:bg-white/20'
            }`}
        >
          <Send
            className={`h-4 w-4 transition-all duration-200 stroke-white ${disabled ? 'opacity-40' : 'opacity-100'}`}
          />
        </Button>
      </div>
    </div>
  );
}
