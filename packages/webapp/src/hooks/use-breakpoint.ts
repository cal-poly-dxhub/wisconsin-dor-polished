import { useState, useEffect } from 'react';

export function useBreakpoint() {
  const [breakpoint, setBreakpoint] = useState<'narrow' | 'wide'>('narrow');

  const updateBreakpoint = () => {
    if (typeof window === 'undefined') return;

    const breakpoints = {
      sm: getComputedStyle(document.documentElement)
        .getPropertyValue('--breakpoint-sm')
        .trim(),
      md: getComputedStyle(document.documentElement)
        .getPropertyValue('--breakpoint-md')
        .trim(),
      lg: getComputedStyle(document.documentElement)
        .getPropertyValue('--breakpoint-lg')
        .trim(),
      xl: getComputedStyle(document.documentElement)
        .getPropertyValue('--breakpoint-xl')
        .trim(),
      '2xl': getComputedStyle(document.documentElement)
        .getPropertyValue('--breakpoint-2xl')
        .trim(),
    };

    // Check if screen is above xl breakpoint (wide)
    let currentBreakpoint: 'narrow' | 'wide' = 'narrow';
    if (window.matchMedia(`(min-width: ${breakpoints.xl})`).matches) {
      currentBreakpoint = 'wide';
    }

    setBreakpoint(currentBreakpoint);
  };

  useEffect(() => {
    // Initial breakpoint check
    updateBreakpoint();

    const handleResize = () => {
      updateBreakpoint();
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, []);

  return breakpoint;
}
