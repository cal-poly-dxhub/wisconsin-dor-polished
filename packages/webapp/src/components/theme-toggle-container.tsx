"use client";

import { ThemeToggle } from "./theme-toggle";

export function ThemeToggleContainer() {
  return (
    <div className="fixed top-4 right-4 z-50">
      <ThemeToggle />
    </div>
  );
}
