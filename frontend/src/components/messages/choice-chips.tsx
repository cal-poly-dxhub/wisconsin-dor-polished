'use client';

import { motion } from 'framer-motion';
import { Button } from '../ui/button';
import { useChatStore } from '@/stores/chat-store';

interface ChoiceChipsProps {
  queryId: string;
  choices: string[];
  onSelect?: (choice: string) => void;
}

export function ChoiceChips({ queryId, choices, onSelect }: ChoiceChipsProps) {
  const queryOrder = useChatStore(s => s.queryOrder);
  const chatState = useChatStore(s => s.chatState);

  const isLastQuery = queryOrder[queryOrder.length - 1] === queryId;
  const disabled = !isLastQuery || chatState !== 'idle' || !onSelect;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className="flex flex-wrap gap-2 mt-4"
    >
      {choices.map((choice) => (
        <Button
          key={choice}
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => onSelect?.(choice)}
          className="rounded-full"
        >
          {choice}
        </Button>
      ))}
    </motion.div>
  );
}
