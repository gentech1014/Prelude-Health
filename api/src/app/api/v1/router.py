"""Aggregates all v1 REST routers under a single prefix."""

from fastapi import APIRouter

from app.api.v1 import (
    appointments,
    booking,
    doctors,
    mock_booking,
    reports,
    sessions,
    uploads,
    webhooks,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(webhooks.router)
api_router.include_router(mock_booking.router)
api_router.include_router(booking.router)
api_router.include_router(doctors.router)
api_router.include_router(sessions.router)
api_router.include_router(appointments.router)
api_router.include_router(reports.router)
api_router.include_router(uploads.router)
