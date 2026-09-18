import {
  Activity,
  CircleHelp,
  Droplet,
  HeartPulse,
  Stethoscope,
  Utensils,
  Wind,
} from 'lucide-react';
import type { JSX } from 'react';
import type { BookingVisitType, CareModality } from '@/services/bookingService';

/**
 * Icon per visit type. Presentation only — the label and description come
 * from the API, so this map never becomes a second source of truth for what
 * the visit types are.
 */
const VISIT_TYPE_ICON: Record<BookingVisitType, JSX.Element> = {
  general_checkup: <Stethoscope size={22} aria-hidden="true" />,
  not_sure: <CircleHelp size={22} aria-hidden="true" />,
  diabetes: <Droplet size={22} aria-hidden="true" />,
  blood_pressure: <Activity size={22} aria-hidden="true" />,
  heart: <HeartPulse size={22} aria-hidden="true" />,
  lung: <Wind size={22} aria-hidden="true" />,
  stomach: <Utensils size={22} aria-hidden="true" />,
};

export function visitTypeIcon(visitType: BookingVisitType): JSX.Element {
  return VISIT_TYPE_ICON[visitType];
}

const MODALITY_LABEL: Record<CareModality, string> = {
  in_person: 'In person',
  virtual: 'Virtual',
  in_person_and_virtual: 'In person and virtual',
};

export function modalityLabel(modality: CareModality): string {
  return MODALITY_LABEL[modality];
}

/**
 * Initials for a provider's avatar. Drops the honorific so "Dr. Emily
 * Carter" reads as EC, not DE, and the badge stays distinguishable between
 * doctors.
 */
export function providerInitials(name: string): string {
  const parts = name
    .split(/\s+/)
    .filter((part) => part.length > 0 && !/^(dr|mr|mrs|ms|prof)\.?$/i.test(part));
  const source = parts.length > 0 ? parts : name.split(/\s+/);
  return source
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join('');
}
