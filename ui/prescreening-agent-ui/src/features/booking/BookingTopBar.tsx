import { FileText, HeartHandshake, Settings, Stethoscope } from 'lucide-react';
import { useEffect, useRef, useState, type JSX } from 'react';
import { ThemeToggle } from '@/components/ThemeToggle';

interface BookingTopBarProps {
  onRegisterAsDoctor: () => void;
  onViewReports: () => void;
}

/**
 * Booking surface top bar: brand, the active nav item, and the settings menu
 * that opens doctor registration and the generated-report lookup. Only real
 * destinations are listed — an item that goes nowhere is worse than an
 * absent one.
 */
export function BookingTopBar({
  onRegisterAsDoctor,
  onViewReports,
}: BookingTopBarProps): JSX.Element {
  return (
    <header className="booking-topbar">
      <div className="booking-brand">
        <span className="booking-brand__mark" aria-hidden="true">
          <HeartHandshake size={20} />
        </span>
        <span>
          <p className="booking-brand__name">CareWeave</p>
          <p className="booking-brand__tagline">Care connects us</p>
        </span>
      </div>

      <div className="booking-topbar__actions">
        <SettingsMenu onRegisterAsDoctor={onRegisterAsDoctor} onViewReports={onViewReports} />
        <ThemeToggle />
      </div>
    </header>
  );
}

/** The gear menu. Closes on outside click and on Escape, and returns focus to its trigger. */
function SettingsMenu({ onRegisterAsDoctor, onViewReports }: BookingTopBarProps): JSX.Element {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    function handlePointerDown(event: MouseEvent): void {
      if (!containerRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape') {
        setIsOpen(false);
        triggerRef.current?.focus();
      }
    }

    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen]);

  return (
    <div className="booking-menu" ref={containerRef}>
      <button
        type="button"
        ref={triggerRef}
        className="booking-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={isOpen}
        aria-label="Settings"
        onClick={() => setIsOpen((open) => !open)}
      >
        <Settings size={18} aria-hidden="true" />
      </button>

      {isOpen ? (
        <div className="booking-menu__panel" role="menu" aria-label="Settings">
          <button
            type="button"
            role="menuitem"
            className="booking-menu__item"
            onClick={() => {
              setIsOpen(false);
              onRegisterAsDoctor();
            }}
          >
            <Stethoscope size={16} aria-hidden="true" />
            Register as doctor
          </button>

          <button
            type="button"
            role="menuitem"
            className="booking-menu__item"
            onClick={() => {
              setIsOpen(false);
              onViewReports();
            }}
          >
            <FileText size={16} aria-hidden="true" />
            View generated report
          </button>
        </div>
      ) : null}
    </div>
  );
}
