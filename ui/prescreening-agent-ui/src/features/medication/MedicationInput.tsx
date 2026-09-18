import { Camera, Check } from 'lucide-react';
import { useId, useRef, type ChangeEvent, type JSX } from 'react';
import { AnswerInput } from '@/components/AnswerInput';
import { InlineBanner } from '@/components/states';
import '@/features/medication/MedicationInput.css';

interface MedicationInputProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  isUploading: boolean;
  uploadedFileName: string | null;
  uploadError: string | null;
  onPhotoSelected: (file: File) => void;
}

/**
 * Medication entry: text that the assistant fills as the patient speaks,
 * plus a photo of the box or label as the alternative.
 *
 * No "Proceed" button. The conversation advances the call, not this
 * screen — a button here would let the patient move on while the
 * assistant was still asking, which is precisely the desync the flow is
 * built to avoid.
 */
export function MedicationInput({
  value,
  onChange,
  onSubmit,
  isUploading,
  uploadedFileName,
  uploadError,
  onPhotoSelected,
}: MedicationInputProps): JSX.Element {
  const textareaId = useId();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    if (file) onPhotoSelected(file);
    event.target.value = '';
  };

  return (
    <div className="medication-input">
      <label htmlFor={textareaId} className="medication-input__label">
        Medications, dose, and how often you take them
      </label>
      <AnswerInput
        id={textareaId}
        value={value}
        onChange={onChange}
        onSubmit={onSubmit}
        placeholder="Say them, or type them here"
        multiline
        rows={3}
      />

      <div className="medication-input__actions">
        <button
          type="button"
          className="medication-input__button medication-input__button--secondary"
          onClick={() => fileInputRef.current?.click()}
          disabled={isUploading}
        >
          <Camera size={16} aria-hidden="true" />
          {isUploading ? 'Uploading…' : 'Add a photo of the box'}
        </button>
      </div>

      {uploadedFileName ? (
        <InlineBanner
          tone="success"
          icon={<Check size={18} aria-hidden="true" />}
          message={`${uploadedFileName} was added for your doctor.`}
        />
      ) : null}

      {uploadError ? <InlineBanner tone="error" message={uploadError} /> : null}

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        onChange={handleFileChange}
        style={{ display: 'none' }}
        aria-hidden="true"
        tabIndex={-1}
      />
    </div>
  );
}
