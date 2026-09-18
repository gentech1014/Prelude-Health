/** One SMS body, split so the intake link can render as an anchor. */
export interface SplitIntakeMessage {
  before: string;
  link: string | null;
  after: string;
}

/**
 * Splits the notification body around its intake link.
 *
 * The message is composed server-side as one string, but the preview has to
 * render the URL as a real anchor rather than inert text — so it is located
 * by exact match rather than by a URL regex, which would also catch any
 * other link the clinic's wording might contain.
 *
 * Falls back to the whole body as prose when the link is absent from it, so
 * a reworded message still renders in full instead of disappearing.
 */
export function splitIntakeMessage(message: string, intakeUrl: string): SplitIntakeMessage {
  const at = intakeUrl === '' ? -1 : message.indexOf(intakeUrl);
  if (at === -1) {
    return { before: message, link: null, after: '' };
  }

  return {
    before: message.slice(0, at),
    link: intakeUrl,
    after: message.slice(at + intakeUrl.length),
  };
}
