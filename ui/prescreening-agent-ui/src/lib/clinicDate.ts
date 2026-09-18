/**
 * Parses the API's `YYYY-MM-DD` clinic date as a local calendar date.
 * `new Date('2026-09-14')` would parse as UTC midnight and render as the
 * previous day for anyone west of Greenwich.
 */
export function parseClinicDate(day: string): Date {
  const [year, month, date] = day.split('-').map(Number);
  return new Date(year ?? 1970, (month ?? 1) - 1, date ?? 1);
}
