import {
  Activity,
  Bandage,
  Brain,
  ClipboardCheck,
  Heart,
  MoreHorizontal,
  ShieldPlus,
} from 'lucide-react';
import type { MultiSelectOption } from '@/components/MultiSelectCardGrid';

export type PatientConcernId =
  | 'pain-or-discomfort'
  | 'injury-follow-up'
  | 'chronic-condition-management'
  | 'routine-check-up'
  | 'mental-health-support'
  | 'preventive-care-or-wellness'
  | 'other';

/**
 * The reasons a patient can pick for their visit.
 *
 * In its own module so the prefill matcher can read these labels without
 * importing the grid: the assistant records the patient's own words and
 * knows nothing about these ids, so the mapping happens here on the
 * frontend against the labels.
 */
export const PATIENT_CONCERN_OPTIONS: MultiSelectOption<PatientConcernId>[] = [
  {
    id: 'pain-or-discomfort',
    label: 'Pain or discomfort',
    icon: <Activity size={18} aria-hidden="true" />,
  },
  {
    id: 'injury-follow-up',
    label: 'Injury follow-up',
    icon: <Bandage size={18} aria-hidden="true" />,
  },
  {
    id: 'chronic-condition-management',
    label: 'Chronic condition management',
    icon: <Heart size={18} aria-hidden="true" />,
  },
  {
    id: 'routine-check-up',
    label: 'Routine check-up',
    icon: <ClipboardCheck size={18} aria-hidden="true" />,
  },
  {
    id: 'mental-health-support',
    label: 'Mental health support',
    icon: <Brain size={18} aria-hidden="true" />,
  },
  {
    id: 'preventive-care-or-wellness',
    label: 'Preventive care or wellness',
    icon: <ShieldPlus size={18} aria-hidden="true" />,
  },
  { id: 'other', label: 'Other', icon: <MoreHorizontal size={18} aria-hidden="true" /> },
];
