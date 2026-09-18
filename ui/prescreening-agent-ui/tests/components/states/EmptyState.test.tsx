import { AlertOctagon } from 'lucide-react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { EmptyState } from '@/components/states/EmptyState';

describe('EmptyState', () => {
  it('renders title, description, and fires the primary action', async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();

    render(
      <EmptyState
        icon={<AlertOctagon aria-hidden="true" />}
        tone="error"
        title="Something went wrong"
        description="We couldn't load this screen."
        primaryAction={{ label: 'Retry', onClick: onRetry }}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong');
    expect(screen.getByText("We couldn't load this screen.")).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('uses status role for neutral tone (empty/no-results) instead of alert', () => {
    render(<EmptyState icon={<AlertOctagon aria-hidden="true" />} title="No visits yet" />);
    expect(screen.getByRole('status')).toBeInTheDocument();
  });
});
