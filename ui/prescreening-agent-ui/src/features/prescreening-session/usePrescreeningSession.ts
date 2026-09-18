import { useContext } from 'react';
import {
  PrescreeningSessionContext,
  type PrescreeningSessionContextValue,
} from '@/features/prescreening-session/prescreeningSessionContext';

export function usePrescreeningSession(): PrescreeningSessionContextValue {
  const context = useContext(PrescreeningSessionContext);
  if (!context) {
    throw new Error('usePrescreeningSession must be used within a PrescreeningSessionProvider');
  }
  return context;
}
