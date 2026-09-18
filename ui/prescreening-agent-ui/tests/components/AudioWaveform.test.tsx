import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AudioWaveform } from '@/components/AudioWaveform';

describe('AudioWaveform', () => {
  it('renders the requested number of bars, colored by source', () => {
    const { container } = render(<AudioWaveform source="user" barCount={7} />);

    const root = container.querySelector('.audio-waveform');
    expect(root).toHaveClass('audio-waveform--user');
    expect(container.querySelectorAll('.audio-waveform__bar')).toHaveLength(7);
  });

  it('shows a static (non-animating) placeholder when inactive', () => {
    const { container } = render(<AudioWaveform source="agent" active={false} barCount={3} />);

    const bars = container.querySelectorAll('.audio-waveform__bar');
    expect(bars).toHaveLength(3);
    bars.forEach((bar) => {
      expect(bar).toHaveClass('audio-waveform__bar--static');
      expect(bar).not.toHaveClass('audio-waveform__bar--idle');
    });
  });

  it('animates the idle placeholder when active with no stream', () => {
    const { container } = render(<AudioWaveform source="agent" active barCount={3} />);

    container.querySelectorAll('.audio-waveform__bar').forEach((bar) => {
      expect(bar).toHaveClass('audio-waveform__bar--idle');
    });
  });
});
