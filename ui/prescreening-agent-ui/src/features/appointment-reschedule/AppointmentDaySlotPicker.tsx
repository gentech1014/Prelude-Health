import { Clock } from 'lucide-react';
import type { JSX } from 'react';
import { EmptyState } from '@/components/states';
import '@/features/appointment-reschedule/AppointmentDaySlotPicker.css';
import { parseClinicDate } from '@/lib/clinicDate';
import type { AppointmentDay } from '@/services/appointmentService';

const FULL_DATE_FORMATTER = new Intl.DateTimeFormat('en-US', {
  weekday: 'long',
  month: 'long',
  day: 'numeric',
});

interface AppointmentDaySlotPickerProps {
  days: readonly AppointmentDay[];
  selectedDay: string | null;
  selectedSlotId: string | null;
  onSelectDay: (day: string) => void;
  onSelectSlot: (slotId: string) => void;
}

/**
 * Date first, then time -- picking a day narrows a long, single scrolling
 * list of every open slot on every date down to one day's times. What
 * used to be a list of every slot on every open date, stacked one below
 * the other, was too long to scroll through comfortably to confirm a slot.
 */
export function AppointmentDaySlotPicker({
  days,
  selectedDay,
  selectedSlotId,
  onSelectDay,
  onSelectSlot,
}: AppointmentDaySlotPickerProps): JSX.Element {
  const activeDay = days.find((day) => day.day === selectedDay) ?? null;

  return (
    <div className="appointment-day-slot-picker">
      <div
        className="appointment-day-slot-picker__strip"
        role="radiogroup"
        aria-label="Available dates"
      >
        {days.map((day) => {
          const isSelected = day.day === selectedDay;
          const isBookable = day.slots.length > 0;

          return (
            <button
              key={day.day}
              type="button"
              className={`appointment-day${isSelected ? ' appointment-day--selected' : ''}`}
              onClick={() => onSelectDay(day.day)}
              disabled={!isBookable}
              aria-pressed={isSelected}
              aria-label={`${FULL_DATE_FORMATTER.format(parseClinicDate(day.day))}${
                isBookable ? '' : ', no times available'
              }`}
            >
              <span className="appointment-day__weekday">{day.weekdayLabel}</span>
              <span className="appointment-day__number">{day.dayLabel}</span>
            </button>
          );
        })}
      </div>

      {activeDay === null ? (
        <p className="appointment-day-slot-picker__hint">Select a date to see open times.</p>
      ) : (
        <>
          <p className="appointment-day-slot-picker__selected-date">
            {FULL_DATE_FORMATTER.format(parseClinicDate(activeDay.day))}
          </p>
          {activeDay.slots.length === 0 ? (
            <EmptyState
              icon={<Clock size={24} aria-hidden="true" />}
              title="No times left on this date"
              description="Choose another date."
            />
          ) : (
            <div
              className="appointment-day-slot-picker__times"
              role="radiogroup"
              aria-label={`Available times on ${FULL_DATE_FORMATTER.format(parseClinicDate(activeDay.day))}`}
            >
              {activeDay.slots.map((slot) => {
                const isSelected = slot.id === selectedSlotId;

                return (
                  <button
                    key={slot.id}
                    type="button"
                    className={`appointment-time${isSelected ? ' appointment-time--selected' : ''}`}
                    onClick={() => onSelectSlot(slot.id)}
                    aria-pressed={isSelected}
                  >
                    {slot.timeRange}
                  </button>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
