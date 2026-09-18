import { Activity, Brain, Droplet, Heart, HeartPulse, MoreHorizontal, Wind } from 'lucide-react';
import type { MultiSelectOption } from '@/components/MultiSelectCardGrid';

export type OngoingConditionId =
  | 'diabetes'
  | 'high-blood-pressure'
  | 'asthma-or-lung-condition'
  | 'heart-disease'
  | 'thyroid-disorder'
  | 'anxiety-or-depression'
  | 'other';

export const ONGOING_CONDITION_OPTIONS: MultiSelectOption<OngoingConditionId>[] = [
  { id: 'diabetes', label: 'Diabetes', icon: <Droplet size={18} aria-hidden="true" /> },
  {
    id: 'high-blood-pressure',
    label: 'High blood pressure',
    icon: <HeartPulse size={18} aria-hidden="true" />,
  },
  {
    id: 'asthma-or-lung-condition',
    label: 'Asthma or lung condition',
    icon: <Wind size={18} aria-hidden="true" />,
  },
  { id: 'heart-disease', label: 'Heart disease', icon: <Heart size={18} aria-hidden="true" /> },
  {
    id: 'thyroid-disorder',
    label: 'Thyroid disorder',
    icon: <Activity size={18} aria-hidden="true" />,
  },
  {
    id: 'anxiety-or-depression',
    label: 'Anxiety or depression',
    icon: <Brain size={18} aria-hidden="true" />,
  },
  { id: 'other', label: 'Other', icon: <MoreHorizontal size={18} aria-hidden="true" /> },
];
