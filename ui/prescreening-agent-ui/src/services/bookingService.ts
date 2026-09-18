import { z } from 'zod';
import { apiClient, ApiError, toApiError } from '@/services/apiClient';

/**
 * Mirrors the backend's `SymptomCategory` — the five clinical categories the
 * pre-screening agent and question bank are keyed by. Parsed rather than
 * trusted: an unknown value must fail at the boundary, not render blank.
 */
export const VISIT_CATEGORIES = ['diabetes', 'blood_pressure', 'heart', 'lung', 'stomach'] as const;

const visitCategorySchema = z.enum(VISIT_CATEGORIES);
export type VisitCategory = z.infer<typeof visitCategorySchema>;

/**
 * Mirrors the backend's `BookingVisitType` — what a patient can actually
 * pick. A superset of the clinical categories: the last two cover a routine
 * checkup and not knowing what is wrong, and map to no category at all.
 */
export const BOOKING_VISIT_TYPES = [...VISIT_CATEGORIES, 'general_checkup', 'not_sure'] as const;

const bookingVisitTypeSchema = z.enum(BOOKING_VISIT_TYPES);
export type BookingVisitType = z.infer<typeof bookingVisitTypeSchema>;

const careModalitySchema = z.enum(['in_person', 'virtual', 'in_person_and_virtual']);
export type CareModality = z.infer<typeof careModalitySchema>;

/** Mirrors the backend's `AvailabilitySource`. */
const availabilitySourceSchema = z.enum(['calendar', 'clinic_hours']);
export type AvailabilitySource = z.infer<typeof availabilitySourceSchema>;

/** Mirrors the backend's `Sex`. */
export const PATIENT_SEXES = ['male', 'female', 'other'] as const;
export type PatientSex = (typeof PATIENT_SEXES)[number];

const visitTypeSchema = z.object({
  visit_type: bookingVisitTypeSchema,
  label: z.string(),
  description: z.string(),
  symptom_category: visitCategorySchema.nullable(),
});

const providerSchema = z.object({
  doctor_id: z.string(),
  name: z.string(),
  credential: z.string().nullable(),
  categories: z.array(visitCategorySchema),
  modality: careModalitySchema,
  calendar_connected: z.boolean(),
  photo_url: z.string().nullable(),
});

const slotSchema = z.object({
  start: z.string(),
  end: z.string(),
  label: z.string(),
});

const availabilityDaySchema = z.object({
  day: z.string(),
  weekday_label: z.string(),
  day_label: z.string(),
  source: availabilitySourceSchema,
  slots: z.array(slotSchema),
});

const availabilitySchema = z.object({
  doctor_id: z.string(),
  timezone: z.string(),
  calendar_connected: z.boolean(),
  days: z.array(availabilityDaySchema),
});

const bookingConfirmationSchema = z.object({
  session_id: z.string(),
  appointment_id: z.string(),
  intake_url: z.string(),
  calendar_event_id: z.string(),
  physician: z.string(),
  scheduled_at: z.string().nullable(),
  patient_id: z.string(),
  patient_id_is_provisional: z.boolean(),
  notification_message: z.string(),
  calendar_synced: z.boolean(),
});

const registrationStartSchema = z.object({ authorization_url: z.string() });

/** One selectable visit type, in the app's own shape. */
export interface VisitType {
  visitType: BookingVisitType;
  label: string;
  description: string;
  /** The clinical category this pre-scopes to, or null for the unscoped options. */
  symptomCategory: VisitCategory | null;
}

export interface Provider {
  doctorId: string;
  name: string;
  credential: string | null;
  categories: VisitCategory[];
  modality: CareModality;
  calendarConnected: boolean;
  /** Absolute portrait URL, or null — the card falls back to initials. */
  photoUrl: string | null;
}

/** One bookable start time. `start` is echoed back verbatim when booking. */
export interface AppointmentTimeSlot {
  start: string;
  label: string;
}

export interface AvailabilityDay {
  /** Calendar date as `YYYY-MM-DD`, in the clinic's timezone. */
  day: string;
  weekdayLabel: string;
  dayLabel: string;
  source: AvailabilitySource;
  slots: AppointmentTimeSlot[];
}

export interface ProviderAvailability {
  doctorId: string;
  timezone: string;
  calendarConnected: boolean;
  days: AvailabilityDay[];
}

export interface BookingConfirmation {
  sessionId: string;
  appointmentId: string;
  intakeUrl: string;
  physician: string;
  scheduledAt: string | null;
  /** The id the session was created under — supplied, or minted for it. */
  patientId: string;
  patientIdIsProvisional: boolean;
  /** The exact SMS body the patient receives, intake link included. */
  notificationMessage: string;
  calendarSynced: boolean;
}

/** What the confirm step submits. Field names mirror the domain, not the wire. */
export interface AppointmentBookingRequest {
  doctorId: string;
  visitType: BookingVisitType;
  slotStart: string;
  patientName: string;
  /** Null when the patient could not recall one — the server mints a provisional id. */
  patientId: string | null;
  dateOfBirth: string;
  sex: PatientSex;
  contactPhone: string | null;
  contactEmail: string | null;
  bookingReason: string | null;
}

export interface DoctorRegistrationRequest {
  name: string;
  credential: string | null;
  categories: VisitCategory[];
  modality: CareModality;
}

async function request<T>(
  schema: z.ZodType<T>,
  call: () => Promise<{ data: unknown }>,
): Promise<T> {
  let payload: unknown;
  try {
    payload = (await call()).data;
  } catch (cause) {
    throw toApiError(cause);
  }

  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    throw new ApiError('malformed', 'We received an unexpected response. Try again shortly.');
  }
  return parsed.data;
}

/** The visit types a patient may book, in the order the clinic presents them. */
export async function fetchVisitTypes(): Promise<VisitType[]> {
  const raw = await request(z.array(visitTypeSchema), () => apiClient.get('/booking/visit-types'));
  return raw.map((entry) => ({
    visitType: entry.visit_type,
    label: entry.label,
    description: entry.description,
    symptomCategory: entry.symptom_category,
  }));
}

/**
 * Providers bookable for one visit type. An unscoped visit type returns every
 * provider — there is no condition to match a doctor's specialties against.
 */
export async function fetchProviders(visitType: BookingVisitType): Promise<Provider[]> {
  const raw = await request(z.array(providerSchema), () =>
    apiClient.get('/booking/providers', { params: { visit_type: visitType } }),
  );
  return raw.map((provider) => ({
    doctorId: provider.doctor_id,
    name: provider.name,
    credential: provider.credential,
    categories: provider.categories,
    modality: provider.modality,
    calendarConnected: provider.calendar_connected,
    photoUrl: provider.photo_url,
  }));
}

/** Bookable slots for one provider, day by day over the clinic's booking horizon. */
export async function fetchProviderAvailability(doctorId: string): Promise<ProviderAvailability> {
  const raw = await request(availabilitySchema, () =>
    apiClient.get(`/booking/providers/${doctorId}/availability`),
  );
  return {
    doctorId: raw.doctor_id,
    timezone: raw.timezone,
    calendarConnected: raw.calendar_connected,
    days: raw.days.map((day) => ({
      day: day.day,
      weekdayLabel: day.weekday_label,
      dayLabel: day.day_label,
      source: day.source,
      slots: day.slots.map((slot) => ({ start: slot.start, label: slot.label })),
    })),
  };
}

/**
 * Books the appointment. The backend re-checks the slot and answers 409
 * (`ApiError` kind `conflict`) if someone else took it in the meantime, so
 * callers must handle that by re-fetching availability rather than retrying.
 */
export async function bookAppointment(
  booking: AppointmentBookingRequest,
): Promise<BookingConfirmation> {
  const raw = await request(bookingConfirmationSchema, () =>
    apiClient.post('/mock-booking/appointments', {
      patient_name: booking.patientName,
      patient_id: booking.patientId,
      date_of_birth: booking.dateOfBirth,
      sex: booking.sex,
      doctor_id: booking.doctorId,
      visit_type: booking.visitType,
      scheduled_at: booking.slotStart,
      booking_reason: booking.bookingReason,
      contact_phone: booking.contactPhone,
      contact_email: booking.contactEmail,
    }),
  );
  return {
    sessionId: raw.session_id,
    appointmentId: raw.appointment_id,
    intakeUrl: raw.intake_url,
    physician: raw.physician,
    scheduledAt: raw.scheduled_at,
    patientId: raw.patient_id,
    patientIdIsProvisional: raw.patient_id_is_provisional,
    notificationMessage: raw.notification_message,
    calendarSynced: raw.calendar_synced,
  };
}

/**
 * Starts doctor registration and returns the Google consent URL to navigate
 * to. Answers 503 (`ApiError` kind `server`) when the deployment has no
 * Google OAuth client configured.
 */
export async function startDoctorRegistration(
  registration: DoctorRegistrationRequest,
): Promise<string> {
  const raw = await request(registrationStartSchema, () =>
    apiClient.post('/doctors/registration', {
      name: registration.name,
      credential: registration.credential,
      categories: registration.categories,
      modality: registration.modality,
    }),
  );
  return raw.authorization_url;
}
