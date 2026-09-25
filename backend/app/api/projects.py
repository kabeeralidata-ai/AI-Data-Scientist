import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.project import Project
from app.models.user import User
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectSummary, ProjectUpdate

router = APIRouter(prefix="/api/projects", tags=["projects"])


def get_owned_project(db: Session, project_id: uuid.UUID, user: User) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


@router.get("", response_model=list[ProjectSummary])
def list_projects(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    projects = (
        db.query(Project)
        .filter(Project.user_id == current_user.id)
        .order_by(Project.updated_at.desc())
        .all()
    )
    summaries = []
    for p in projects:
        summaries.append(
            ProjectSummary(
                **ProjectResponse.model_validate(p).model_dump(),
                dataset_count=len(p.datasets),
                model_count=len(p.models),
                report_count=len(p.reports),
            )
        )
    return summaries


@router.post("", response_model=ProjectResponse, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = Project(user_id=current_user.id, name=payload.name.strip(), description=payload.description)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return get_owned_project(db, project_id, current_user)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_owned_project(db, project_id, current_user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    project = get_owned_project(db, project_id, current_user)
    db.delete(project)
    db.commit()
