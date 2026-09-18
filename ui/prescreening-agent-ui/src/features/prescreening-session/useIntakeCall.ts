import { useContext } from 'react';
import {
  IntakeCallContext,
  type IntakeCallValue,
} from '@/features/prescreening-session/intakeCallContext';

export function useIntakeCall(): IntakeCallValue {
  const context = useContext(IntakeCallContext);
  if (!context) {
    throw new Error('useIntakeCall must be used within an IntakeCallProvider');
  }
  return context;
}
