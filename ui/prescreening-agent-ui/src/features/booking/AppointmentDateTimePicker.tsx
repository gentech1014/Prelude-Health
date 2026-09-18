import { CalendarX, ChevronLeft, ChevronRight } from 'lucide-react';
import { useState, type JSX } from 'react';
import { EmptyState } from '@/components/states';
import { parseClinicDate } from '@/lib/clinicDate';
import type { AvailabilityDay } from '@/services/bookingService';

const DAYS_PER_PAGE = 7;

const MONTH_FORMATTER = new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' });
const FULL_DATE_FORMATTER = new Intl.DateTimeFormat('en-US', {
  weekday: 'long',
  month: 'long',
  day: 'numeric',
});

interface AppointmentDateTimePickerProps {
  days: readonly AvailabilityDay[];
  selectedDay: string | null;
  selectedSlotStart: string | null;
  onSelectDay: (day: string) => void;
  onSelectSlot: (slotStart: string) => void;
}

/**
 * The date strip and time grid. Pages through the booking horizon a week at
 * a time; days the clinic is closed (or fully booked) stay visible but
 * disabled, so the strip keeps its calendar alignment instead of silently
 * skipping dates.
 */
export function AppointmentDateTimePicker({
  days,
  selectedDay,
  selectedSlotStart,
  onSelectDay,
  onSelectSlot,
}: AppointmentDateTimePickerProps): JSX.Element {
  const [pageStart, setPageStart] = useState(0);

  const visibleDays = days.slice(pageStart, pageStart + DAYS_PER_PAGE);
  const firstVisible = visibleDays[0];
  const activeDay = days.find((day) => day.day === selectedDay) ?? null;

  return (
    <>
      <div className="booking-date-head">
        <button
          type="button"
          className="booking-stepper-button"
          onClick={() => setPageStart((start) => Math.max(0, start - DAYS_PER_PAGE))}
          disabled={pageStart === 0}
          aria-label="Earlier dates"
        >
          <ChevronLeft size={16} aria-hidden="true" />
        </button>
        <span className="booking-date-head__month">
          {firstVisible ? MONTH_FORMATTER.format(parseClinicDate(firstVisible.day)) : '—'}
        </span>
        <button
          type="button"
          className="booking-stepper-button"
          onClick={() =>
            setPageStart((start) =>
              start + DAYS_PER_PAGE < days.length ? start + DAYS_PER_PAGE : start,
            )
          }
          disabled={pageStart + DAYS_PER_PAGE >= days.length}
          aria-label="Later dates"
        >
          <ChevronRight size={16} aria-hidden="true" />
        </button>
      </div>

      <div className="booking-date-strip">
        {visibleDays.map((day) => {
          const isSelected = day.day === selectedDay;
          const isBookable = day.slots.length > 0;

          return (
            <button
              key={day.day}
              type="button"
              className={`booking-date${isSelected ? ' booking-date--selected' : ''}`}
              onClick={() => onSelectDay(day.day)}
              disabled={!isBookable}
              aria-pressed={isSelected}
              aria-label={`${FULL_DATE_FORMATTER.format(parseClinicDate(day.day))}${
                isBookable ? '' : ', no times available'
              }`}
            >
              <span className="booking-date__weekday">{day.weekdayLabel}</span>
              <span className="booking-date__day">{day.dayLabel}</span>
            </button>
          );
        })}
      </div>

      {activeDay === null ? (
        <p className="booking-section__hint">Select a date to see available times.</p>
      ) : activeDay.slots.length === 0 ? (
        <EmptyState
          icon={<CalendarX size={24} aria-hidden="true" />}
          title="No times left on this date"
          description="Choose another date, or a different provider."
        />
      ) : (
        <div className="booking-time-grid" role="radiogroup" aria-label="Available times">
          {activeDay.slots.map((slot) => {
            const isSelected = slot.start === selectedSlotStart;

            return (
              <button
                key={slot.start}
                type="button"
                className={`booking-time${isSelected ? ' booking-time--selected' : ''}`}
                onClick={() => onSelectSlot(slot.start)}
                aria-pressed={isSelected}
              >
                {slot.label}
              </button>
            );
          })}
        </div>
      )}
    </>
  );
}
