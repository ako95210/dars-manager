from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_client
from .database import get_db
from .jobs import TERMINAL_STATES
from .media_lifecycle import meter_media
from .models import Artifact, Asset, Project, User
from .runtime import manager, media_storage


router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=4000)

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("Le titre ne peut pas être vide")
        return title

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return value.strip()


class ProjectUpdate(ProjectCreate):
    pass


class ProjectResponse(BaseModel):
    id: str
    title: str
    description: str
    created_at: datetime
    updated_at: datetime


def project_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        title=project.title,
        description=project.description,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("", response_model=list[ProjectResponse])
def list_projects(user: User = Depends(require_client), db: Session = Depends(get_db)) -> list[ProjectResponse]:
    projects = db.scalars(
        select(Project).where(Project.user_id == user.id).order_by(Project.updated_at.desc())
    ).all()
    return [project_response(project) for project in projects]


@router.post("", response_model=ProjectResponse, status_code=201)
def create_project(
    payload: ProjectCreate,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> ProjectResponse:
    project = Project(
        user_id=user.id,
        title=payload.title,
        description=payload.description,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project_response(project)


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: str,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> ProjectResponse:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project_response(project)


@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> ProjectResponse:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    project.title = payload.title
    project.description = payload.description
    db.commit()
    db.refresh(project)
    return project_response(project)


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    user: User = Depends(require_client),
    db: Session = Depends(get_db),
) -> None:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    jobs = manager.list_for_user(user.id, project.id)
    for job in jobs:
        if job.tool == "archive_export" and job.options.get("automatic") and job.state == "queued":
            manager.cancel(job)
    if any(job.state not in TERMINAL_STATES for job in jobs):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Un traitement est encore actif pour ce projet",
        )
    media_rows = [
        *db.scalars(select(Asset).where(Asset.project_id == project.id)).all(),
        *db.scalars(select(Artifact).where(Artifact.project_id == project.id)).all(),
    ]
    for media_row in media_rows:
        meter_media(db, media_row)
    # Release the media row locks before manager.delete() removes jobs in its own session.
    # Otherwise PostgreSQL waits on our transaction when cascading to artifacts.
    db.commit()
    for media_row in media_rows:
        if not media_row.storage_key:
            continue
        try:
            media_storage.delete(media_row.storage_key)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Le média temporaire n'a pas pu être supprimé; réessayez.",
            ) from exc
    for job in jobs:
        manager.delete(job)
    db.delete(project)
    db.commit()
