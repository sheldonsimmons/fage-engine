import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// Deterministic name -> categorical chart slot (1-8), so a given
// department/model name always gets the same color across renders and
// re-sorted lists -- identity, not rank, drives the color (dataviz
// skill's "color follows the entity, never its rank"). A plain string
// hash rather than array index, so filtering the list to fewer rows
// doesn't repaint the survivors into different colors.
export function categoricalColorVar(name: string): string {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) | 0
  }
  const slot = (Math.abs(hash) % 8) + 1
  return `var(--chart-${slot})`
}
