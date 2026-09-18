import { describe, expect, it } from 'vitest';
import {
  buildIntakeSocketUrl,
  intakeClientEvent,
  parseIntakeServerEvent,
} from '@/features/prescreening-session/intakeProtocol';

/**
 * The socket carries the whole call, so this boundary is where an
 * unrecognized frame has to be caught. A frame that slips through
 * unvalidated surfaces as an undefined three components deep, mid-call.
 */

describe('parseIntakeServerEvent', () => {
  it('accepts the first frame with its resume position and prefill', () => {
    const event = parseIntakeServerEvent({
      type: 'connected',
      protocol_version: 2,
      screen: 'medication',
      prefill: { allergies: { allergies: 'penicillin' } },
      symptoms: { current: null, answers: [], answered_count: 0 },
      audio: { sample_rate: 16000, channels: 1, format: 'pcm16' },
    });

    expect(event).toMatchObject({ type: 'connected', screen: 'medication' });
  });

  it('rejects a screen the app does not route', () => {
    // A screen name the frontend has no route for would navigate the
    // patient nowhere and desync the call from what they can see.
    expect(
      parseIntakeServerEvent({ type: 'navigate', screen: 'prescriptions', sequence: 1 }),
    ).toBeNull();
  });

  it('rejects a frame missing a field the app would then read as undefined', () => {
    expect(parseIntakeServerEvent({ type: 'transcript', role: 'agent' })).toBeNull();
    expect(parseIntakeServerEvent({ type: 'agent_audio', audio: 'AAAA' })).toBeNull();
  });

  it('returns null for a type this build does not know, rather than throwing', () => {
    // An older browser against a newer server must not lose a call that is
    // otherwise working.
    expect(parseIntakeServerEvent({ type: 'something_new' })).toBeNull();
    expect(parseIntakeServerEvent(null)).toBeNull();
    expect(parseIntakeServerEvent('not a frame')).toBeNull();
  });

  it('keeps a non-final transcript distinguishable from a finished one', () => {
    const partial = parseIntakeServerEvent({
      type: 'transcript',
      role: 'patient',
      text: 'about two wee',
      is_final: false,
    });

    expect(partial).toMatchObject({ is_final: false });
  });
});

describe('intakeClientEvent', () => {
  it('names the screen and field on a typed answer, so it can be labelled in the transcript', () => {
    expect(intakeClientEvent.formUpdate('medication', 'medications', 'Aspirin')).toEqual({
      type: 'client_form_update',
      screen: 'medication',
      field: 'medications',
      value: 'Aspirin',
    });
  });

  it('acks the screen the browser is actually showing', () => {
    expect(intakeClientEvent.screenAck('allergies')).toEqual({
      type: 'client_screen_ack',
      screen: 'allergies',
    });
  });
});

describe('buildIntakeSocketUrl', () => {
  it('escapes both the session id and the ticket', () => {
    const url = buildIntakeSocketUrl('sess/1', 'ws.1.a+b.sig');

    expect(url).toContain('/ws/intake/sess%2F1');
    expect(url).toContain('ticket=ws.1.a%2Bb.sig');
  });
});
