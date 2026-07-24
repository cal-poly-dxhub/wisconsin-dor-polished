'use client';

export function ThinkingPane({ text }: { text: string }) {
  return (
    <div className="flex flex-col px-5 py-5">
      <h2 className="text-xl font-bold text-neutral-900">Thinking</h2>
      <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-neutral-900">
        {text}
      </p>
    </div>
  );
}
