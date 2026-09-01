/**
 * Conditional class names.
 *
 * `clsx` was approved by the brief, but it is a dependency for four lines, and
 * §9.7 caps added JS. This is those four lines.
 */
export type ClassValue = string | false | null | undefined;

export function cx(...values: ClassValue[]): string {
  return values.filter(Boolean).join(" ");
}
