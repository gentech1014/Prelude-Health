import { Clock, MapPin, Stethoscope, User } from 'lucide-react';
import type { JSX, ReactNode } from 'react';
import '@/features/appointment-schedule/AppointmentDetailsCard.css';

/**
 * The scheduled visit, as the card renders it.
 *
 * The nullable fields are nullable on purpose: the backend genuinely does
 * not always know the clinician's credential, why the visit was booked, or
 * the facility's address, and a card that fills those in with something
 * plausible is indistinguishable from one showing a real record.
 */
export interface AppointmentSummary {
  weekday: string;
  day: string;
  year: string;
  clinician: string;
  clinicianCredential: string | null;
  timeRange: string;
  duration: string;
  visitReason: string | null;
  location: string | null;
  locationDetail: string | null;
}

interface AppointmentDetailsCardProps {
  appointment: AppointmentSummary;
}

interface DetailRow {
  icon: ReactNode;
  title: string;
  subtitle: string | null;
}

/** The scheduled visit's date, clinician, time, reason, and location. */
export function AppointmentDetailsCard({ appointment }: AppointmentDetailsCardProps): JSX.Element {
  const rows: DetailRow[] = [
    {
      icon: <User size={18} aria-hidden="true" />,
      title: appointment.clinician,
      subtitle: appointment.clinicianCredential,
    },
    {
      icon: <Clock size={18} aria-hidden="true" />,
      title: appointment.timeRange,
      subtitle: appointment.duration,
    },
  ];

  if (appointment.visitReason) {
    rows.push({
      icon: <Stethoscope size={18} aria-hidden="true" />,
      title: 'Reason for visit',
      subtitle: appointment.visitReason,
    });
  }

  if (appointment.location) {
    rows.push({
      icon: <MapPin size={18} aria-hidden="true" />,
      title: appointment.location,
      subtitle: appointment.locationDetail,
    });
  }

  return (
    <div className="appointment-details-card">
      <div className="appointment-details-card__date">
        <span className="appointment-details-card__date-weekday">{appointment.weekday}</span>
        <span className="appointment-details-card__date-day">{appointment.day}</span>
        <span className="appointment-details-card__date-year">{appointment.year}</span>
      </div>

      <div className="appointment-details-card__rows">
        {rows.map((row) => (
          <div className="appointment-details-card__row" key={row.title}>
            <span className="appointment-details-card__row-icon" aria-hidden="true">
              {row.icon}
            </span>
            <div>
              <p className="appointment-details-card__row-title">{row.title}</p>
              {row.subtitle ? (
                <p className="appointment-details-card__row-subtitle">{row.subtitle}</p>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
