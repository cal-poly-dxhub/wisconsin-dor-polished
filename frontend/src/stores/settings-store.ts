'use client';

import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type Persona = 'citizen' | 'government';

interface SettingsState {
  detailedTrace: boolean;
  setDetailedTrace: (value: boolean) => void;
  autoScroll: boolean;
  setAutoScroll: (value: boolean) => void;
  persona: Persona;
  setPersona: (value: Persona) => void;
}

const isDev =
  typeof window !== 'undefined' &&
  (window.location.hostname === 'localhost' ||
    window.location.hostname === '127.0.0.1');

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      detailedTrace: isDev,
      setDetailedTrace: (value) => set({ detailedTrace: value }),
      autoScroll: true,
      setAutoScroll: (value) => set({ autoScroll: value }),
      persona: 'citizen',
      setPersona: (value) => set({ persona: value }),
    }),
    {
      name: 'wisco:settings',
    }
  )
);
