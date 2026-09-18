import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ModelAwakeningLoader } from '@/components/ModelAwakeningLoader';

describe('ModelAwakeningLoader', () => {
  it('exposes a default accessible status when no label is given', () => {
    render(<ModelAwakeningLoader />);
    expect(screen.getByRole('status')).toHaveTextContent('Connecting to your assistant');
  });

  it('shows the given status label instead once wired in', () => {
    render(<ModelAwakeningLoader label="Preparing your assistant…" />);
    expect(screen.getByRole('status')).toHaveTextContent('Preparing your assistant…');
  });
});
