import { Brain, Dna, Droplet, Heart, HeartPulse, MoreHorizontal, Zap } from 'lucide-react';
import type { MultiSelectOption } from '@/components/MultiSelectCardGrid';

export type FamilyHistoryConditionId =
  | 'diabetes'
  | 'heart-disease'
  | 'high-blood-pressure'
  | 'cancer'
  | 'stroke'
  | 'mental-health-conditions'
  | 'other';

export const FAMILY_HISTORY_OPTIONS: MultiSelectOption<FamilyHistoryConditionId>[] = [
  { id: 'diabetes', label: 'Diabetes', icon: <Droplet size={18} aria-hidden="true" /> },
  { id: 'heart-disease', label: 'Heart disease', icon: <Heart size={18} aria-hidden="true" /> },
  {
    id: 'high-blood-pressure',
    label: 'High blood pressure',
    icon: <HeartPulse size={18} aria-hidden="true" />,
  },
  { id: 'cancer', label: 'Cancer', icon: <Dna size={18} aria-hidden="true" /> },
  { id: 'stroke', label: 'Stroke', icon: <Zap size={18} aria-hidden="true" /> },
  {
    id: 'mental-health-conditions',
    label: 'Mental health conditions',
    icon: <Brain size={18} aria-hidden="true" />,
  },
  { id: 'other', label: 'Other', icon: <MoreHorizontal size={18} aria-hidden="true" /> },
];
