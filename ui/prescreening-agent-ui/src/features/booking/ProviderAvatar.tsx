import { useState, type JSX } from 'react';
import { providerInitials } from '@/features/booking/visitTypePresentation';

interface ProviderAvatarProps {
  name: string;
  photoUrl: string | null;
}

/**
 * A provider's portrait, falling back to their initials.
 *
 * The fallback is not decoration: a doctor with no photo on file, or a photo
 * host that fails, must still produce a readable card rather than a broken
 * image icon. `onError` covers the second case, which no amount of checking
 * `photoUrl` can predict.
 */
export function ProviderAvatar({ name, photoUrl }: ProviderAvatarProps): JSX.Element {
  const [hasFailed, setHasFailed] = useState(false);

  if (photoUrl === null || hasFailed) {
    return (
      <span className="booking-provider__avatar" aria-hidden="true">
        {providerInitials(name)}
      </span>
    );
  }

  return (
    <span className="booking-provider__avatar booking-provider__avatar--photo">
      <img
        src={photoUrl}
        // Decorative: the provider's name is adjacent text, so alt text here
        // would only make a screen reader say it twice.
        alt=""
        aria-hidden="true"
        loading="lazy"
        decoding="async"
        width={56}
        height={56}
        onError={() => setHasFailed(true)}
      />
    </span>
  );
}
