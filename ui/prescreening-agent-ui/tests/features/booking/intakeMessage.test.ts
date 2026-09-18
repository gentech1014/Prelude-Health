import { describe, expect, it } from 'vitest';
import { splitIntakeMessage } from '@/features/booking/intakeMessage';

const URL = 'https://example.test/prescreen/sess_1?token=abc';

describe('splitIntakeMessage', () => {
  it('splits the body around the intake link', () => {
    const result = splitIntakeMessage(`Book here: ${URL} Thanks.`, URL);

    expect(result).toEqual({ before: 'Book here: ', link: URL, after: ' Thanks.' });
  });

  it('keeps the whole body as prose when the link is absent', () => {
    // A reworded message must still render in full rather than vanish.
    const result = splitIntakeMessage('Your appointment is confirmed.', URL);

    expect(result).toEqual({
      before: 'Your appointment is confirmed.',
      link: null,
      after: '',
    });
  });

  it('does not treat an empty url as a match at position zero', () => {
    const result = splitIntakeMessage('Your appointment is confirmed.', '');

    expect(result.link).toBeNull();
    expect(result.before).toBe('Your appointment is confirmed.');
  });
});
