"""/courses: danh sách / tạo / sửa / xoá môn học."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import select

from app.context import AppContext
from app.db.models import Course, LectureSession
from app.db.session import db_session
from app.routers.deps import api_error, get_ctx
from app.schemas import CourseIn, course_out

router = APIRouter()


@router.get("/courses")
def list_courses(ctx: AppContext = Depends(get_ctx)) -> list[dict[str, Any]]:
    with db_session(ctx.engine) as s:
        courses = list(s.exec(select(Course).order_by(Course.name)))
        counts = dict(
            s.exec(
                select(LectureSession.course_id, func.count(LectureSession.id)).group_by(LectureSession.course_id)
            ).all()
        )
    return [course_out(c, session_count=counts.get(c.id, 0)) for c in courses]


@router.post("/courses", status_code=201)
def create_course(body: CourseIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    course = Course(name=body.name.strip(), name_it=(body.name_it or "").strip() or None, lecturer=body.lecturer)
    with db_session(ctx.engine) as s:
        s.add(course)
        s.commit()
        s.refresh(course)
    return course_out(course, session_count=0)


@router.put("/courses/{course_id}")
def update_course(course_id: int, body: CourseIn, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        course = s.get(Course, course_id)
        if course is None:
            raise api_error(404, "COURSE_NOT_FOUND", "Không tìm thấy môn học")
        course.name = body.name.strip()
        course.name_it = (body.name_it or "").strip() or None
        course.lecturer = (body.lecturer or "").strip() or None
        s.add(course)
        s.commit()
        s.refresh(course)
    ctx.context.invalidate()
    return course_out(course)


@router.delete("/courses/{course_id}")
def delete_course(course_id: int, ctx: AppContext = Depends(get_ctx)) -> dict[str, Any]:
    with db_session(ctx.engine) as s:
        course = s.get(Course, course_id)
        if course is None:
            raise api_error(404, "COURSE_NOT_FOUND", "Không tìm thấy môn học")
        s.delete(course)
        s.commit()
    ctx.context.invalidate()
    return {"deleted": True}
