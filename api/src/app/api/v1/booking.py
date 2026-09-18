"""Read side of the booking screen: visit types, providers, availability.

Everything the booking UI renders comes from here rather than from
constants compiled into the frontend -- the visit types are this service's
own `SymptomCategory` vocabulary (the same one the live agent and question
bank use), the providers are the `doctors` collection, and the slots are
computed against real calendars. Submitting a booking stays in
`app.api.v1.mock_booking`, which owns the simulated scheduling platform.

Unauthenticated by design, like a public scheduling page: nothing served
here is PHI. It exposes only the doctor-facing facts a patient needs to
choose an appointment, and `ProviderOption` explicitly cannot carry a
doctor's stored Google credentials.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_availability_service, get_doctor_repository
from app.core.constants import BOOKING_VISIT_TYPES, BookingVisitType
from app.core.exceptions import DoctorNotFoundError
from app.repositories.doctor_repository import DoctorRepository
from app.schemas.booking_ui import (
    ProviderAvailabilityResponse,
    ProviderOption,
    VisitTypeOption,
)
from app.services.availability_service import AvailabilityService

router = APIRouter(prefix="/booking", tags=["booking"])


@router.get("/visit-types", response_model=list[VisitTypeOption])
async def list_visit_types() -> list[VisitTypeOption]:
    """The visit types a patient may book, in the order they are shown.

    Includes the two unscoped options -- a routine checkup, and not knowing
    what is wrong -- which map to no symptom category. See
    `BookingVisitType` for why those are not clinical categories.
    """
    return [
        VisitTypeOption(
            visit_type=visit_type,
            label=copy.label,
            description=copy.description,
            symptom_category=copy.symptom_category,
        )
        for visit_type, copy in BOOKING_VISIT_TYPES.items()
    ]


@router.get("/providers", response_model=list[ProviderOption])
async def list_providers(
    doctor_repository: Annotated[DoctorRepository, Depends(get_doctor_repository)],
    visit_type: Annotated[
        BookingVisitType | None,
        Query(description="Restrict to providers who accept this visit type."),
    ] = None,
) -> list[ProviderOption]:
    """Providers bookable for a visit type, or every provider when unfiltered.

    An unscoped visit type (general checkup, not sure) returns every
    provider: there is no condition to match a doctor's specialties against,
    and narrowing the list on a guess would hide clinicians who can help.
    """
    category = BOOKING_VISIT_TYPES[visit_type].symptom_category if visit_type else None
    doctors = await doctor_repository.list_for_category(category)
    return [ProviderOption.from_doctor(doctor) for doctor in doctors]


@router.get("/providers/{doctor_id}/availability", response_model=ProviderAvailabilityResponse)
async def get_provider_availability(
    doctor_id: str,
    doctor_repository: Annotated[DoctorRepository, Depends(get_doctor_repository)],
    availability_service: Annotated[AvailabilityService, Depends(get_availability_service)],
    from_day: Annotated[
        date | None, Query(alias="from", description="First date to return. Defaults to today.")
    ] = None,
    days: Annotated[int | None, Query(ge=1, le=90, description="How many days to return.")] = None,
) -> ProviderAvailabilityResponse:
    """Bookable slots for one provider, day by day.

    Raises 404 when `doctor_id` is unknown. Days with no open slots are
    still returned, so the date strip can show them as unavailable rather
    than skipping dates and misaligning the calendar.
    """
    doctor = await doctor_repository.get_by_id(doctor_id)
    if doctor is None:
        raise DoctorNotFoundError(doctor_id)

    available_days = await availability_service.days_for_doctor(
        doctor, from_day=from_day, days=days
    )
    return ProviderAvailabilityResponse.build(
        doctor,
        timezone=str(availability_service.clinic_zone),
        days=available_days,
    )
