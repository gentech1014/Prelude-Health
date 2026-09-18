import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { FAMILY_HISTORY_OPTIONS } from '@/features/family-social-history/familyHistoryOptions';
import { ONGOING_CONDITION_OPTIONS } from '@/features/medical-history/conditionOptions';
import { PATIENT_CONCERN_OPTIONS } from '@/features/patient-concerns/patientConcernOptions';

/**
 * The agent now names option ids, and the server refuses any it does not
 * recognize. That makes the option vocabulary a contract between two
 * codebases: an option added here and not to `SCREEN_FIELD_OPTIONS` is one
 * the server silently drops, so the card never ticks and nothing anywhere
 * reports why.
 *
 * `contracts/intake-field-options.json` is generated from the backend
 * registry by `prescreening-agent-api/scripts/export_field_options.py`.
 * This asserts the frontend's own lists against it; the API's
 * `test_field_option_contract.py` asserts the file is not stale. Between
 * them, drifting in either direction is a failing test rather than a
 * card that quietly stops working.
 */

interface FieldContract {
  screen: string;
  options: string[];
}

function readContract(): { fields: Record<string, FieldContract> } {
  const raw: unknown = JSON.parse(
    readFileSync(resolve(__dirname, '../../../../contracts/intake-field-options.json'), 'utf8'),
  );
  return raw as { fields: Record<string, FieldContract> };
}

const contract = readContract();

function contractOptions(field: string): string[] {
  const entry = contract.fields[field];
  if (entry === undefined) throw new Error(`no contract entry for '${field}'`);
  return entry.options;
}

describe('intake field option contract', () => {
  it.each([
    ['concerns', PATIENT_CONCERN_OPTIONS],
    ['conditions', ONGOING_CONDITION_OPTIONS],
    ['family_conditions', FAMILY_HISTORY_OPTIONS],
  ])('%s offers exactly the ids the server will accept', (field, options) => {
    expect(options.map((option) => option.id).sort()).toEqual([...contractOptions(field)].sort());
  });

  it.each([
    ['tobacco_use', ['Never', 'Former', 'Current']],
    ['alcohol_use', ['Never', 'Occasional', 'Regular']],
  ])('%s uses its visible labels as its ids', (field, labels) => {
    // These rows have no id/label split: `SingleChoicePills` renders the
    // label and reports it back as the value, so the contract has to carry
    // the labels verbatim or the agent's id will never equal the value.
    expect(contractOptions(field)).toEqual(labels);
  });

  it.each(['no_known_allergies', 'no_hospital_stays', 'saw_other_provider', 'had_recent_tests'])(
    '%s is a yes/no field, answering its own name as a proposition',
    (field) => {
      expect(contractOptions(field)).toEqual(['yes', 'no']);
    },
  );
});
