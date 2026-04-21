'use client';

import { motion } from 'framer-motion';

export function LoadingStrip() {
  return (
    <div className="mt-4 grid grid-cols-2 gap-1.5 w-fit">
      {[0, 1, 2, 3].map(i => (
        <motion.div
          key={i}
          className="h-3.5 w-3.5 rounded-sm bg-muted-foreground/40"
          animate={{ opacity: [0.3, 1, 0.3] }}
          transition={{
            duration: 1.4,
            repeat: Infinity,
            ease: 'easeInOut',
            delay: i * 0.15,
          }}
        />
      ))}
    </div>
  );
}
