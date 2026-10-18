import { endOfISOWeek, endOfMonth, format, parse, startOfISOWeek, startOfMonth, subMonths, subWeeks } from 'date-fns'

export type ReportCadence = 'weekly' | 'monthly'

export function previousReportPeriod(cadence: ReportCadence): string {
  return cadence === 'weekly'
    ? format(subWeeks(new Date(), 1), "RRRR-'W'II")
    : format(subMonths(new Date(), 1), 'yyyy-MM')
}

export function reportPeriodRange(cadence: ReportCadence, value: string): { start: string; end: string } | null {
  const pattern = cadence === 'weekly' ? "RRRR-'W'II" : 'yyyy-MM'
  const date = parse(value, pattern, new Date())
  if (Number.isNaN(date.getTime()) || format(date, pattern) !== value) return null
  return {
    start: format(cadence === 'weekly' ? startOfISOWeek(date) : startOfMonth(date), 'yyyy-MM-dd'),
    end: format(cadence === 'weekly' ? endOfISOWeek(date) : endOfMonth(date), 'yyyy-MM-dd'),
  }
}
