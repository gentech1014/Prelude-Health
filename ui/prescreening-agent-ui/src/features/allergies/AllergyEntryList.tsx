import { motion } from 'framer-motion';
import { Plus, X } from 'lucide-react';
import { useId, type JSX } from 'react';
import { AnswerInput } from '@/components/AnswerInput';
import type { RecordedPair } from '@/features/prescreening-session/prefillMatching';
import '@/features/allergies/AllergyEntryList.css';
import { LIST_ITEM_VARIANTS, LIST_STAGGER_VARIANTS } from '@/lib/pageMotion';

/** One allergen/reaction row. `primary` is the allergen, `secondary` the reaction. */
export type AllergyEntry = RecordedPair & { id: string };

interface AllergyEntryListProps {
  hasNoKnownAllergies: boolean;
  onToggleNoKnownAllergies: (value: boolean) => void;
  entries: readonly AllergyEntry[];
  onEntryChange: (id: string, part: 'primary' | 'secondary', value: string) => void;
  onRemoveEntry: (id: string) => void;
  onAddEntry: () => void;
  /** Sends every row as typed so far. Shared by both fields in every row. */
  onSubmitEntries: () => void;
}

/** Captures one or more allergen/reaction pairs, or a "none known" opt-out. */
export function AllergyEntryList({
  hasNoKnownAllergies,
  onToggleNoKnownAllergies,
  entries,
  onEntryChange,
  onRemoveEntry,
  onAddEntry,
  onSubmitEntries,
}: AllergyEntryListProps): JSX.Element {
  return (
    <div className="allergy-entry-list">
      <label className="allergy-entry-list__nka">
        <input
          type="checkbox"
          checked={hasNoKnownAllergies}
          onChange={(event) => onToggleNoKnownAllergies(event.target.checked)}
          style={{ width: 20, height: 20, accentColor: 'var(--color-primary)', flexShrink: 0 }}
        />
        <span className="allergy-entry-list__nka-text">I don&apos;t have any known allergies</span>
      </label>

      {hasNoKnownAllergies ? null : (
        <motion.div
          className="allergy-entry-list__entries"
          variants={LIST_STAGGER_VARIANTS}
          initial="hidden"
          animate="visible"
        >
          {entries.map((entry, index) => (
            <motion.div key={entry.id} variants={LIST_ITEM_VARIANTS}>
              <AllergyEntryCard
                entry={entry}
                index={index}
                canRemove={entries.length > 1}
                onChange={onEntryChange}
                onRemove={onRemoveEntry}
                onSubmit={onSubmitEntries}
              />
            </motion.div>
          ))}

          <button type="button" className="allergy-entry-list__add" onClick={onAddEntry}>
            <Plus size={16} aria-hidden="true" />
            Add another allergy
          </button>
        </motion.div>
      )}
    </div>
  );
}

interface AllergyEntryCardProps {
  entry: AllergyEntry;
  index: number;
  canRemove: boolean;
  onChange: (id: string, part: 'primary' | 'secondary', value: string) => void;
  onRemove: (id: string) => void;
  onSubmit: () => void;
}

function AllergyEntryCard({
  entry,
  index,
  canRemove,
  onChange,
  onRemove,
  onSubmit,
}: AllergyEntryCardProps): JSX.Element {
  const allergenId = useId();
  const reactionId = useId();

  return (
    <div className="allergy-entry-card">
      <div className="allergy-entry-card__header">
        <span className="allergy-entry-card__index">Allergy {index + 1}</span>
        {canRemove ? (
          <button
            type="button"
            className="allergy-entry-card__remove"
            onClick={() => onRemove(entry.id)}
            aria-label={`Remove allergy ${index + 1}`}
          >
            <X size={14} aria-hidden="true" />
          </button>
        ) : null}
      </div>

      <div className="allergy-entry-card__field">
        <label className="allergy-entry-card__field-label" htmlFor={allergenId}>
          Allergen
        </label>
        <AnswerInput
          id={allergenId}
          value={entry.primary}
          onChange={(value) => onChange(entry.id, 'primary', value)}
          onSubmit={onSubmit}
          placeholder="For example penicillin, peanuts, latex"
        />
      </div>

      <div className="allergy-entry-card__field">
        <label className="allergy-entry-card__field-label" htmlFor={reactionId}>
          Reaction
        </label>
        <AnswerInput
          id={reactionId}
          value={entry.secondary}
          onChange={(value) => onChange(entry.id, 'secondary', value)}
          onSubmit={onSubmit}
          placeholder="What happens when you are exposed to it"
        />
      </div>
    </div>
  );
}
