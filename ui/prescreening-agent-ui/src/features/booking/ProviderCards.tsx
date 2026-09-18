import { Check } from 'lucide-react';
import type { JSX } from 'react';
import { ProviderAvatar } from '@/features/booking/ProviderAvatar';
import { modalityLabel } from '@/features/booking/visitTypePresentation';
import type { Provider } from '@/services/bookingService';

interface ProviderCardsProps {
  providers: readonly Provider[];
  selectedDoctorId: string | null;
  onSelect: (doctorId: string) => void;
}

/**
 * Single-select provider row. Shows only facts the system actually holds —
 * name, credential, how they see patients, and whether their own calendar
 * is connected. No ratings or review counts: this service has no such data,
 * and inventing them would be a fabricated clinical credential.
 */
export function ProviderCards({
  providers,
  selectedDoctorId,
  onSelect,
}: ProviderCardsProps): JSX.Element {
  return (
    <div
      className="booking-option-grid booking-option-grid--providers"
      role="radiogroup"
      aria-label="Provider"
    >
      {providers.map((provider) => {
        const isSelected = provider.doctorId === selectedDoctorId;

        return (
          <label
            key={provider.doctorId}
            className={`booking-provider${isSelected ? ' booking-provider--selected' : ''}`}
          >
            <input
              type="radio"
              name="provider"
              className="booking-option__input"
              value={provider.doctorId}
              checked={isSelected}
              onChange={() => onSelect(provider.doctorId)}
            />
            <ProviderAvatar name={provider.name} photoUrl={provider.photoUrl} />
            <span className="booking-provider__body">
              <span className="booking-provider__name">
                {provider.name}
                {provider.credential ? `, ${provider.credential}` : ''}
              </span>
              <span className="booking-provider__meta">{modalityLabel(provider.modality)}</span>
              <span className="booking-provider__chips">
                <span
                  className={`booking-chip${provider.calendarConnected ? ' booking-chip--verified' : ''}`}
                >
                  {provider.calendarConnected ? 'Calendar connected' : 'Clinic hours'}
                </span>
              </span>
            </span>
            {isSelected ? (
              <span className="booking-provider__check" aria-hidden="true">
                <Check size={14} />
              </span>
            ) : null}
          </label>
        );
      })}
    </div>
  );
}
