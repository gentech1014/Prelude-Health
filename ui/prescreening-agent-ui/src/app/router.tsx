import { createBrowserRouter, Navigate } from 'react-router-dom';
import { RootLayout } from '@/app/RootLayout';
import { MobileOnlyGate } from '@/components/MobileOnlyGate';
import { PrescreeningSessionProvider } from '@/features/prescreening-session/PrescreeningSessionProvider';
import { Allergies } from '@/pages/Allergies';
import { BookAppointment } from '@/pages/BookAppointment';
import { AppBoot } from '@/pages/AppBoot';
import { AppointmentReschedule } from '@/pages/AppointmentReschedule';
import { AppointmentSchedule } from '@/pages/AppointmentSchedule';
import { ConfirmDetails } from '@/pages/ConfirmDetails';
import { FamilySocialHistory } from '@/pages/FamilySocialHistory';
import { Medication } from '@/pages/Medication';
import { MedicalHistory } from '@/pages/MedicalHistory';
import { PatientConcerns } from '@/pages/PatientConcerns';
import { RecentCare } from '@/pages/RecentCare';
import { ReportLookup } from '@/pages/ReportLookup';
import { SymptomStory } from '@/pages/SymptomStory';
import { ThankYou } from '@/pages/ThankYou';
import { ThemePreview } from '@/pages/ThemePreview';
import { Welcome } from '@/pages/Welcome';
import { EndCall } from '@/pages/EndCall';

export const router = createBrowserRouter([
  {
    element: <RootLayout />,
    children: [
      {
        // Booking is the product's front door now -- a patient with no
        // link yet lands here to make an appointment, not on a dead-end
        // "open your link" placeholder.
        path: '/',
        element: <Navigate to="/book" replace />,
      },
      {
        // The entire prescreening call lives under this one route — every
        // step below is a child of the same session, never its own top-level page.
        // Portrait-mobile only, unlike booking: the call is the patient's own
        // phone experience, so desktop gets the "continue on your phone" screen.
        path: '/prescreen/:sessionId',
        element: (
          <MobileOnlyGate>
            <PrescreeningSessionProvider />
          </MobileOnlyGate>
        ),
        children: [
          { index: true, element: <AppBoot /> },
          { path: 'welcome', element: <Welcome /> },
          { path: 'confirm-details', element: <ConfirmDetails /> },
          { path: 'appointment-schedule', element: <AppointmentSchedule /> },
          { path: 'appointment-reschedule', element: <AppointmentReschedule /> },
          { path: 'patient-concerns', element: <PatientConcerns /> },
          { path: 'symptom-story', element: <SymptomStory /> },
          { path: 'medication', element: <Medication /> },
          { path: 'allergies', element: <Allergies /> },
          { path: 'medical-history', element: <MedicalHistory /> },
          { path: 'recent-care', element: <RecentCare /> },
          { path: 'family-social-history', element: <FamilySocialHistory /> },
          { path: 'thank-you', element: <ThankYou /> },
          { path: 'end-call', element: <EndCall /> },
        ],
      },
      {
        // Booking is its own surface: a responsive desktop/mobile screen for
        // scheduling an appointment, which then creates the prescreening session.
        path: '/book',
        element: <BookAppointment />,
      },
      {
        path: '/reports',
        element: <ReportLookup />,
      },
      {
        path: '/style-guide',
        element: <ThemePreview />,
      },
    ],
  },
]);
