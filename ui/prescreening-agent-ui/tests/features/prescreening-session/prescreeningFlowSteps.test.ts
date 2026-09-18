import { describe, expect, it } from 'vitest';
import {
  buildPrescreeningSessionRootPath,
  buildPrescreeningStepPath,
  PRESCREENING_FLOW_STEPS,
} from '@/features/prescreening-session/prescreeningFlowSteps';

describe('prescreeningFlowSteps', () => {
  it('orders the linear flow exactly as the enforced call sequence', () => {
    expect(PRESCREENING_FLOW_STEPS).toEqual([
      'welcome',
      'confirm-details',
      'appointment-schedule',
      'patient-concerns',
      'symptom-story',
      'medication',
      'allergies',
      'medical-history',
      'recent-care',
      'family-social-history',
      'thank-you',
    ]);
  });

  it('builds a session-relative path for a linear step', () => {
    expect(buildPrescreeningStepPath('abc-123', 'medication')).toBe(
      '/prescreen/abc-123/medication',
    );
  });

  it('builds a session-relative path for a branch step', () => {
    expect(buildPrescreeningStepPath('abc-123', 'appointment-reschedule')).toBe(
      '/prescreen/abc-123/appointment-reschedule',
    );
  });

  it("builds the session's own root path", () => {
    expect(buildPrescreeningSessionRootPath('abc-123')).toBe('/prescreen/abc-123');
  });
});
