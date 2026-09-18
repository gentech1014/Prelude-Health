import { Camera, FileText, Upload, X } from 'lucide-react';
import { useRef, type ChangeEvent, type JSX } from 'react';
import '@/features/recent-care/MedicalReportUpload.css';

interface MedicalReportUploadProps {
  fileName: string | null;
  /** Disables the controls and shows a spinner label while bytes are in flight. */
  isUploading?: boolean;
  /** Optional upload error message to surface to the user. */
  error?: string | null;
  onFileSelected: (file: File) => void;
  onRemove: () => void;
}

/** Upload a photo or copy of a recent report — a lab result, imaging summary, or discharge note. */
export function MedicalReportUpload({
  fileName,
  isUploading = false,
  error = null,
  onFileSelected,
  onRemove,
}: MedicalReportUploadProps): JSX.Element {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    if (file) onFileSelected(file);
    // Reset so the same file can be re-selected if removed and re-added
    event.target.value = '';
  };

  if (fileName) {
    return (
      <div className="mru">
        <div className="mru__file">
          <FileText size={18} aria-hidden="true" className="mru__file-icon" />
          <span className="mru__file-name">{fileName}</span>
          <button
            type="button"
            className="mru__file-remove"
            onClick={onRemove}
            aria-label="Remove uploaded report"
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>
        {error && <p className="mru__error">{error}</p>}
      </div>
    );
  }

  return (
    <div className="mru">
      {isUploading ? (
        <div className="mru__uploading">
          <span className="mru__uploading-spinner" aria-hidden="true" />
          <span>Uploading your file…</span>
        </div>
      ) : (
        <>
          <p className="mru__hint">
            Upload a photo or file of your test result — a lab result, X-ray
            report, scan summary, or discharge note.
          </p>

          <div className="mru__actions">
            {/* ── Upload a file from device storage ── */}
            <button
              type="button"
              className="mru__action-btn mru__action-btn--upload"
              onClick={() => fileInputRef.current?.click()}
            >
              <span className="mru__action-icon">
                <Upload size={22} aria-hidden="true" />
              </span>
              <span className="mru__action-label">Upload file</span>
              <span className="mru__action-sub">PDF, JPG or PNG</span>
            </button>

            {/* ── Take a photo with the device camera ── */}
            <button
              type="button"
              className="mru__action-btn mru__action-btn--camera"
              onClick={() => cameraInputRef.current?.click()}
            >
              <span className="mru__action-icon">
                <Camera size={22} aria-hidden="true" />
              </span>
              <span className="mru__action-label">Take photo</span>
              <span className="mru__action-sub">Use your camera</span>
            </button>
          </div>
        </>
      )}

      {error && <p className="mru__error">{error}</p>}

      {/* Hidden: general file picker (PDF + images) */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*,.pdf"
        onChange={handleFileChange}
        style={{ display: 'none' }}
        aria-hidden="true"
        tabIndex={-1}
      />

      {/* Hidden: camera-only input — on desktop this falls back to the file picker */}
      <input
        ref={cameraInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        onChange={handleFileChange}
        style={{ display: 'none' }}
        aria-hidden="true"
        tabIndex={-1}
      />
    </div>
  );
}
