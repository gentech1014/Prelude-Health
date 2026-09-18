import { CheckCircle2 } from 'lucide-react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { ToastProvider } from '@/components/states/ToastProvider';
import { useToast } from '@/hooks/useToast';

function TriggerButton(): JSX.Element {
  const { showToast } = useToast();
  return (
    <button
      type="button"
      onClick={() =>
        showToast({
          icon: <CheckCircle2 aria-hidden="true" />,
          tone: 'success',
          message: 'Appointment confirmed.',
        })
      }
    >
      Confirm
    </button>
  );
}

function DefaultIconTriggerButton(): JSX.Element {
  const { showToast } = useToast();
  return (
    <button
      type="button"
      onClick={() => showToast({ tone: 'error', message: 'Something failed.' })}
    >
      Fail
    </button>
  );
}

describe('ToastProvider / useToast', () => {
  it('shows a toast on demand and dismisses it via its close button', async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <TriggerButton />
      </ToastProvider>,
    );

    await user.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(screen.getByText('Appointment confirmed.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Dismiss' }));
    await waitFor(() =>
      expect(screen.queryByText('Appointment confirmed.')).not.toBeInTheDocument(),
    );
  });

  it('falls back to a tone-appropriate default icon when none is given', async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <DefaultIconTriggerButton />
      </ToastProvider>,
    );

    await user.click(screen.getByRole('button', { name: 'Fail' }));
    const toast = screen.getByRole('status');
    expect(toast).toHaveTextContent('Something failed.');
    // A default icon (an <svg>) should be present even though the caller didn't pass one.
    expect(toast.querySelector('svg')).toBeInTheDocument();
  });
});
