export function ChatSkeleton() {
  return (
    <div className="flex h-full flex-col items-center justify-start gap-6 px-6 pt-8">
      <div className="w-full max-w-2xl space-y-6 animate-pulse">
        {/* User message */}
        <div className="flex justify-end">
          <div className="h-4 w-48 rounded-full bg-black/10 dark:bg-muted/60" />
        </div>
        {/* Assistant response */}
        <div className="space-y-2">
          <div className="h-4 w-full rounded-full bg-black/[0.08] dark:bg-muted/50" />
          <div className="h-4 w-3/4 rounded-full bg-black/[0.08] dark:bg-muted/50" />
          <div className="h-4 w-5/6 rounded-full bg-black/[0.08] dark:bg-muted/50" />
        </div>
        {/* User message */}
        <div className="flex justify-end">
          <div className="h-4 w-36 rounded-full bg-black/10 dark:bg-muted/60" />
        </div>
        {/* Assistant response */}
        <div className="space-y-2">
          <div className="h-4 w-full rounded-full bg-black/[0.08] dark:bg-muted/50" />
          <div className="h-4 w-2/3 rounded-full bg-black/[0.08] dark:bg-muted/50" />
        </div>
      </div>
    </div>
  );
}
