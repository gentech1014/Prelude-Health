import { motion } from 'framer-motion';
import { Plus, X } from 'lucide-react';
import { useId, type JSX } from 'react';
import { AnswerInput } from '@/components/AnswerInput';
import type { RecordedPair } from '@/features/prescreening-session/prefillMatching';
import '@/features/medical-history/HospitalStayList.css';
import { LIST_ITEM_VARIANTS, LIST_STAGGER_VARIANTS } from '@/lib/pageMotion';

/** One hospital stay. `primary` is the reason, `secondary` roughly when. */
export type HospitalStayEntry = RecordedPair & { id: string };

interface HospitalStayListProps {
  hasNoHospitalStays: boolean;
  onToggleNoHospitalStays: (value: boolean) => void;
  entries: readonly HospitalStayEntry[];
  onEntryChange: (id: string, part: 'primary' | 'secondary', value: string) => void;
  onRemoveEntry: (id: string) => void;
  onAddEntry: () => void;
  /** Sends every row as typed so far. Shared by both fields in every row. */
  onSubmitEntries: () => void;
}

/** Captures one or more relevant hospital stays, or a "none" opt-out. */
export function HospitalStayList({
  hasNoHospitalStays,
  onToggleNoHospitalStays,
  entries,
  onEntryChange,
  onRemoveEntry,
  onAddEntry,
  onSubmitEntries,
}: HospitalStayListProps): JSX.Element {
  return (
    <div className="hospital-stay-list">
      <label className="hospital-stay-list__none">
        <input
          type="checkbox"
          checked={hasNoHospitalStays}
          onChange={(event) => onToggleNoHospitalStays(event.target.checked)}
          style={{ width: 20, height: 20, accentColor: 'var(--color-primary)', flexShrink: 0 }}
        />
        <span className="hospital-stay-list__none-text">
          I haven&apos;t had any relevant hospital stays
        </span>
      </label>

      {hasNoHospitalStays ? null : (
        <motion.div
          className="hospital-stay-list__entries"
          variants={LIST_STAGGER_VARIANTS}
          initial="hidden"
          animate="visible"
        >
          {entries.map((entry, index) => (
            <motion.div key={entry.id} variants={LIST_ITEM_VARIANTS}>
              <HospitalStayCard
                entry={entry}
                index={index}
                canRemove={entries.length > 1}
                onChange={onEntryChange}
                onRemove={onRemoveEntry}
                onSubmit={onSubmitEntries}
              />
            </motion.div>
          ))}

          <button type="button" className="hospital-stay-list__add" onClick={onAddEntry}>
            <Plus size={16} aria-hidden="true" />
            Add another hospital stay
          </button>
        </motion.div>
      )}
    </div>
  );
}

interface HospitalStayCardProps {
  entry: HospitalStayEntry;
  index: number;
  canRemove: boolean;
  onChange: (id: string, part: 'primary' | 'secondary', value: string) => void;
  onRemove: (id: string) => void;
  onSubmit: () => void;
}

function HospitalStayCard({
  entry,
  index,
  canRemove,
  onChange,
  onRemove,
  onSubmit,
}: HospitalStayCardProps): JSX.Element {
  const reasonId = useId();
  const timeframeId = useId();

  return (
    <div className="hospital-stay-card">
      <div className="hospital-stay-card__header">
        <span className="hospital-stay-card__index">Hospital stay {index + 1}</span>
        {canRemove ? (
          <button
            type="button"
            className="hospital-stay-card__remove"
            onClick={() => onRemove(entry.id)}
            aria-label={`Remove hospital stay ${index + 1}`}
          >
            <X size={14} aria-hidden="true" />
          </button>
        ) : null}
      </div>

      <div className="hospital-stay-card__field">
        <label className="hospital-stay-card__field-label" htmlFor={reasonId}>
          Reason for stay
        </label>
        <AnswerInput
          id={reasonId}
          value={entry.primary}
          onChange={(value) => onChange(entry.id, 'primary', value)}
          onSubmit={onSubmit}
          placeholder="For example appendectomy, childbirth, pneumonia"
        />
      </div>

      <div className="hospital-stay-card__field">
        <label className="hospital-stay-card__field-label" htmlFor={timeframeId}>
          When (approximately)
        </label>
        <AnswerInput
          id={timeframeId}
          value={entry.secondary}
          onChange={(value) => onChange(entry.id, 'secondary', value)}
          onSubmit={onSubmit}
          placeholder="For example two years ago, or March 2023"
        />
      </div>
    </div>
  );
}
